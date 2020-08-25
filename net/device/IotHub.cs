using Mash.Logging;
using Microsoft.Azure.Devices.Client;
using Microsoft.Azure.Devices.Shared;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace Microsoft.Azure.Iot.Thief.Device
{
    internal class IotHub : IIotHub, IDisposable
    {
        private readonly string _deviceConnectionString;
        private readonly TransportType _transportType;
        private readonly Logger _logger;

        private SemaphoreSlim _lifetimeControl = new SemaphoreSlim(1, 1);

        private const string _contentEncoding = "utf-8";
        private const string _contentType = "application/json";

        private volatile bool _isConnected;
        private volatile bool _wasEverConnected;
        private volatile ConnectionStatus _connectionStatus;
        private volatile DeviceClient _deviceClient;

        private static readonly JsonSerializerOptions _jsonSerializerOptions = new JsonSerializerOptions { IgnoreNullValues = true };

        public IDictionary<string, string> IotProperties { get; } = new Dictionary<string, string>();

        public IotHub(Logger logger, string deviceConnectionString, TransportType transportType)
        {
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _deviceConnectionString = deviceConnectionString;
            _transportType = transportType;
            _deviceClient = null;
        }

        public async Task InitializeAsync()
        {
            await _lifetimeControl.WaitAsync().ConfigureAwait(false);

            try
            {
                if (Cleanup())
                {
                    _deviceClient = DeviceClient.CreateFromConnectionString(_deviceConnectionString, _transportType);
                    _deviceClient.SetConnectionStatusChangesHandler(ConnectionStatusChangesHandler);
                    await _deviceClient.OpenAsync().ConfigureAwait(false);
                }
            }
            finally
            {
                _lifetimeControl.Release();
            }
        }

        public bool Cleanup(bool force = false)
        {
            if (_deviceClient != null
                && _wasEverConnected
                && (force || _connectionStatus == ConnectionStatus.Disconnected))
            {
                _deviceClient.Dispose();
                _deviceClient = null;
                _wasEverConnected = false;
                _logger.Trace($"IotHub cleaned up");
                return true;
            }

            _logger.Trace($"IotHub not cleaned up: device client instance {_deviceClient}, was ever connected {_wasEverConnected}, connection status {_connectionStatus}");
            return false;
        }

        public async Task SendTelemetryAsync(
            object telemetryObject,
            IDictionary<string, string> extraProperties,
            CancellationToken cancellationToken)
        {
            Debug.Assert(_deviceClient != null);
            Debug.Assert(telemetryObject != null);

            string message = JsonSerializer.Serialize(telemetryObject, _jsonSerializerOptions);
            Debug.Assert(!string.IsNullOrWhiteSpace(message));

            var iotMessage = new Message(Encoding.UTF8.GetBytes(message))
            {
                ContentEncoding = _contentEncoding,
                ContentType = _contentType,
            };

            foreach (var prop in IotProperties)
            {
                iotMessage.Properties.TryAdd(prop.Key, prop.Value);
            }

            if (extraProperties != null)
            {
                foreach (var prop in extraProperties)
                {
                    // Use TryAdd to ensure the attempt does not fail with an exception
                    // in the event that this key already exists in this dictionary,
                    // in which case it'll silently fail.
                    iotMessage.Properties.TryAdd(prop.Key, prop.Value);
                }
            }

            await _deviceClient.SendEventAsync(iotMessage, cancellationToken).ConfigureAwait(false);
            _logger.Trace($"Sent message [{message}]");
        }

        public async Task SetPropertiesAsync(object properties, CancellationToken cancellationToken)
        {
            Debug.Assert(_deviceClient != null);
            Debug.Assert(properties != null);

            string propertiesPayload = JsonSerializer.Serialize(properties, _jsonSerializerOptions);
            Debug.Assert(!string.IsNullOrWhiteSpace(propertiesPayload));

            await _deviceClient
                .UpdateReportedPropertiesAsync(
                    new TwinCollection(propertiesPayload),
                    cancellationToken)
                .ConfigureAwait(false);
        }

        private void ConnectionStatusChangesHandler(ConnectionStatus status, ConnectionStatusChangeReason reason)
        {
            _logger.Trace($"Connection status changed: status=[{status}], reason=[{reason}]", TraceSeverity.Information);

            _connectionStatus = status;
            _isConnected = status == ConnectionStatus.Connected;

            switch (status)
            {
                case ConnectionStatus.Connected:
                    // The DeviceClient has connected.
                    _wasEverConnected = true;
                    break;

                case ConnectionStatus.Disconnected_Retrying:
                    // The DeviceClient is retrying based on the retry policy. Just wait.
                    break;

                case ConnectionStatus.Disabled:
                    // The DeviceClient has been closed gracefully. Do nothing.
                    break;

                case ConnectionStatus.Disconnected:
                    switch (reason)
                    {
                        case ConnectionStatusChangeReason.Bad_Credential:
                            // The supplied credentials were invalid. Fix the input and then create a new device client instance.
                            break;

                        case ConnectionStatusChangeReason.Device_Disabled:
                            // The device has been deleted or marked as disabled (on your hub instance).
                            // Fix the device status in Azure and then create a new device client instance.
                            break;

                        case ConnectionStatusChangeReason.Retry_Expired:
                            // The DeviceClient has been disconnected because the retry policy expired.
                            // If you want to perform more operations on the device client, you should dispose (DisposeAsync()) and then open (OpenAsync()) the client.

                            _ = InitializeAsync();
                            break;

                        case ConnectionStatusChangeReason.Communication_Error:
                            // The DeviceClient has been disconnected due to a non-retry-able exception. Inspect the exception for details.
                            // If you want to perform more operations on the device client, you should dispose (DisposeAsync()) and then open (OpenAsync()) the client.

                            _ = InitializeAsync();
                            break;

                        default:
                            _logger.Trace("This combination of ConnectionStatus and ConnectionStatusChangeReason is not expected", TraceSeverity.Critical);
                            break;
                    }

                    break;

                default:
                    _logger.Trace("This combination of ConnectionStatus and ConnectionStatusChangeReason is not expected", TraceSeverity.Critical);
                    break;
            }
        }

        public void Dispose()
        {
            if (_lifetimeControl != null)
            {
                _lifetimeControl.Dispose();
                _lifetimeControl = null;
            }

            Cleanup(true);

            _logger.Trace($"IotHub instance disposed");

        }
    }
}
