# Code design principles

* A device client from one language should be able to work in tandem with a service client written in a different language.
* The client apps should be both "test collateral" and also "sample code" which act as examples of a complex system written using our SDK.
* We should reasonably expect (and encourage) customers to copy/paste the client apps.
* Any "test-specific" parts of the apps should be factored into their own modules for easy removal. e.g. code to send telemetry to Azure Monitor should be very loosely coupled from code that communicates with IoTHub
