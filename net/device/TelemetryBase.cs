using System;
using System.Text.Json.Serialization;

namespace Microsoft.Azure.Iot.Thief.Device
{
    internal abstract class TelemetryBase
    {
        /// <summary>
        /// The date/time the event occurred, in UTC.
        /// </summary>
        [JsonPropertyName("eventDateTimeUtc")]
        public DateTime? EventDateTimeUtc { get; set; } = DateTime.UtcNow;
    }
}
