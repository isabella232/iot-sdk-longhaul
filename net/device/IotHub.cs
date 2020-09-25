using Mash.Logging;
using Microsoft.Azure.Devices.Client;
using Microsoft.Azure.Devices.Client.Exceptions;
using Microsoft.Azure.Devices.Shared;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
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
        private volatile int _connectionStatusChangeCount = 0;
        private readonly Stopwatch _disconnectedTimer = new Stopwatch();
        private ConnectionStatus _disconnectedStatus;
        private ConnectionStatusChangeReason _disconnectedReason;
        private volatile DeviceClient _deviceClient;

        private readonly ConcurrentQueue<Message> _messagesToSend = new ConcurrentQueue<Message>();
        private readonly List<Message> _pendingMessages = new List<Message>();
        private long _totalMessagesSent = 0;

        private static readonly JsonSerializerOptions _jsonSerializerOptions = new JsonSerializerOptions { IgnoreNullValues = true };

        public IDictionary<string, string> IotProperties { get; } = new Dictionary<string, string>();

        public IotHub(Logger logger, string deviceConnectionString, TransportType transportType)
        {
            _logger = logger ?? throw new ArgumentNullException(nameof(logger));
            _deviceConnectionString = deviceConnectionString;
            _transportType = transportType;
            _deviceClient = null;
        }

        /// <summary>
        /// Initializes the connection to IoT Hub.
        /// </summary>
        public async Task InitializeAsync()
        {
            await _lifetimeControl.WaitAsync().ConfigureAwait(false);

            try
            {
                if (_deviceClient == null
                    || ResetClient())
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

        /// <summary>
        /// Runs a background
        /// </summary>
        /// <param name="ct">The cancellation token</param>
        public async Task RunAsync(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested)
            {
                // Wait when there are few messages to send, or if not connected
                if (_messagesToSend.Count < 10
                    || !_isConnected)
                {
                    try
                    {
                        await Task.Delay(60000, ct).ConfigureAwait(false);
                    }
                    catch (TaskCanceledException)
                    {
                        // App is signalled to exit
                        return;
                    }
                }

                _logger.Metric("MessageBacklog", _messagesToSend.Count);

                // If not connected, skip the work below this round
                if (!_isConnected)
                {
                    _logger.Trace($"Waiting for connection before batching telemetry", TraceSeverity.Warning);
                    continue;
                }

                // Make a batch of messages to send, unless we're retrying a previous batch
                if (!_pendingMessages.Any()
                    && _messagesToSend.Any())
                {
                    int batchSizeBytes = 0;

                    while (_messagesToSend.TryPeek(out Message nextMessage))
                    {
                        int nextMessageSize = MessageHelpers.GetMessagePayloadSize(nextMessage);
                        if (batchSizeBytes + nextMessageSize > MessageHelpers.MaxMessagePayloadBytes)
                        {
                            break;
                        }

                        batchSizeBytes += nextMessageSize;
                        _messagesToSend.TryDequeue(out Message thisMessage);
                        _pendingMessages.Add(thisMessage);
                    }

                    _logger.Metric("MessageBacklog", _messagesToSend.Count);
                    _logger.Metric("BatchSizeBytes", batchSizeBytes);
                }

                // Send any messages prepped to send
                if (_pendingMessages.Any())
                {
                    _logger.Metric("PendingMessages", _pendingMessages.Count);

                    try
                    {
                        await _deviceClient.SendEventBatchAsync(_pendingMessages).ConfigureAwait(false);

                        _totalMessagesSent += _pendingMessages.Count;
                        _logger.Metric("TotalMessagesSent", _totalMessagesSent);

                        foreach (var message in _pendingMessages)
                        {
                            message.Dispose();
                        }
                        _pendingMessages.Clear();
                    }
                    catch (IotHubException ex) when (ex.IsTransient)
                    {
                        _logger.Trace($"Caught transient exception; will retry: {ex}", TraceSeverity.Warning);
                    }
                    catch (Exception ex) when (ExceptionHelper.IsNetworkExceptionChain(ex))
                    {
                        _logger.Trace($"A network-related exception was caught; will retry: {ex}", TraceSeverity.Warning);
                    }
                    catch (Exception ex)
                    {
                        _logger.Trace($"Unknown error batching telemetry: {ex}", TraceSeverity.Critical);
                    }
                }

                _logger.Metric("PendingMessages", _pendingMessages.Count);
            }
        }

        public void AddTelemetry(
            TelemetryBase telemetryObject,
            IDictionary<string, string> extraProperties = null)
        {
            Debug.Assert(_deviceClient != null);
            Debug.Assert(telemetryObject != null);

            // Save off the event time, or use "now" if not specified
            var creationTimeUtc = telemetryObject.EventDateTimeUtc ?? DateTime.UtcNow;
            // Remove it so it does not get serialized in the message
            telemetryObject.EventDateTimeUtc = null;

            string message = JsonSerializer.Serialize(telemetryObject, _jsonSerializerOptions);
            Debug.Assert(!string.IsNullOrWhiteSpace(message));

            var iotMessage = new Message(Encoding.UTF8.GetBytes(message))
            {
                ContentEncoding = _contentEncoding,
                ContentType = _contentType,
                MessageId = Guid.NewGuid().ToString(),
                // Add the event time to the system property
                CreationTimeUtc = creationTimeUtc,
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
                    // in which case it'll log an error.
                    if (!iotMessage.Properties.TryAdd(prop.Key, prop.Value))
                    {
                        _logger.Trace($"Could not add telemetry property {prop.Key} due to conflict.", TraceSeverity.Error);
                    }
                }
            }

            // Worker feeding off this queue will dispose the messages when they are sent
            _messagesToSend.Enqueue(iotMessage);
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

        public void Dispose()
        {
            _logger.Trace("Disposing");

            if (_lifetimeControl != null)
            {
                _lifetimeControl.Dispose();
                _lifetimeControl = null;
            }

            ResetClient(true);

            _logger.Trace($"IotHub instance disposed");

        }

        private bool ResetClient(bool force = false)
        {
            if (_deviceClient != null
                && _wasEverConnected
                && (force || _connectionStatus == ConnectionStatus.Disconnected))
            {
                _deviceClient?.Dispose();
                _deviceClient = null;
                _wasEverConnected = false;
                _logger.Trace($"IotHub reset");
                return true;
            }

            _logger.Trace($"IotHub not reset: device client instance {_deviceClient}, was ever connected {_wasEverConnected}, connection status {_connectionStatus}");
            return false;
        }

        private void ConnectionStatusChangesHandler(ConnectionStatus status, ConnectionStatusChangeReason reason)
        {
            _logger.Trace($"Connection status changed ({++_connectionStatusChangeCount}): status=[{status}], reason=[{reason}]", TraceSeverity.Information);

            _connectionStatus = status;
            _isConnected = status == ConnectionStatus.Connected;

            if (_isConnected && _disconnectedTimer.IsRunning)
            {
                _disconnectedTimer.Stop();
                _logger.Metric(
                    "DisconnectedDurationMinutes",
                    _disconnectedTimer.Elapsed.TotalMinutes,
                    new Dictionary<string, string>
                    {
                        { "DisconnectedStatus", _disconnectedStatus.ToString() },
                        { "DisconnectedReason", _disconnectedReason.ToString() },
                    });
            }
            else if (!_isConnected && !_disconnectedTimer.IsRunning)
            {
                _disconnectedTimer.Restart();
                _disconnectedStatus = status;
                _disconnectedReason = reason;
            }

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
    }
}
