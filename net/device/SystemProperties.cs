﻿using System.Runtime.InteropServices;
using System.Text.Json.Serialization;

namespace Microsoft.Azure.Iot.Thief.Device
{
    class SystemProperties
    {
        [JsonPropertyName("systemArchitecture")]
        public string SystemArchitecture { get; set; } = RuntimeInformation.OSArchitecture.ToString();

        [JsonPropertyName("osVersion")]
        public string OsVersion { get; set; } = RuntimeInformation.OSDescription;

        [JsonPropertyName("frameworkDescription")]
        public string FrameworkDescription { get; set; } = RuntimeInformation.FrameworkDescription;
    }
}
