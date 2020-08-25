using System.Diagnostics;
using System.Text.Json.Serialization;

namespace Microsoft.Azure.Iot.Thief.Device
{
    internal class SystemHealthTelemetry
    {
        private static readonly string _processName = Process.GetCurrentProcess().ProcessName;

        private static readonly PerformanceCounter _processCpuCounter = new PerformanceCounter(
            "Process",
            "% Processor Time",
            _processName);
        private static readonly PerformanceCounter _processWorkingSet = new PerformanceCounter(
            "Process",
            "Working Set",
            _processName);

        [JsonPropertyName("processCpuUsagePercent")]
        public float? ProcessCpuUsagePercent { get; set; } = _processCpuCounter.NextValue();

        [JsonPropertyName("processWorkingSet")]
        public float? ProcessWorkingSet { get; set; } = _processWorkingSet.NextValue();

        [JsonPropertyName("systemAvailableMemory")]
        public float? SystemAvailableMemory { get; set; }

        [JsonPropertyName("systemFreeMemory")]
        public float? SystemFreeMemory { get; set; }
    }
}
