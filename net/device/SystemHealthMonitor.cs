using Mash.Logging;
using System;
using System.Threading;
using System.Threading.Tasks;

namespace Microsoft.Azure.Iot.Thief.Device
{
    /// <summary>
    /// Acts as a sensor for the device, but what it "senses" is system and process health.
    /// In this way we can have the SDK be used for various functionality, but also get reports
    /// of its health.
    /// </summary>
    internal class SystemHealthMonitor
    {
        private readonly IIotHub _iotHub;
        private readonly Logger _logger;
        private static readonly TimeSpan _interval = TimeSpan.FromSeconds(10);

        public SystemHealthMonitor(IIotHub iotHub, Logger logger)
        {
            _iotHub = iotHub;
            _logger = logger;
        }

        public async Task RunAsync(CancellationToken cancellationToken)
        {
            await _iotHub
                .SetPropertiesAsync(new SystemHealthProperties(), cancellationToken)
                .ConfigureAwait(false);

            while (!cancellationToken.IsCancellationRequested)
            {
                await _iotHub
                    .SendTelemetryAsync(new SystemHealthTelemetry(), null, cancellationToken)
                    .ConfigureAwait(false);
                await Task.Delay(_interval, cancellationToken).ConfigureAwait(false);
            }
        }
    }
}
