using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace Microsoft.Azure.Iot.Thief.Device
{
    /// <summary>
    /// An interface for device code to interact with hub
    /// </summary>
    internal interface IIotHub
    {
        /// <summary>
        /// Sends the specified telemetry object as a message.
        /// </summary>
        /// <param name="telemetryObject">An object to be converted to an application/json payload to send.</param>
        /// <param name="cancellationToken">A cancellation token.</param>
        Task SendTelemetryAsync(object telemetryObject, IDictionary<string, string> extraProperties = null, CancellationToken cancellationToken = default);

        /// <summary>
        /// Sets the specified properties on the device twin.
        /// </summary>
        /// <param name="properties">Twin properties to set.</param>
        /// <param name="cancellationToken">A cancellation token.</param>
        Task SetPropertiesAsync(object properties, CancellationToken cancellationToken = default);
    }
}