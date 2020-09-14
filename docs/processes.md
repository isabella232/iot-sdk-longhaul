# Processes and interactions

* Each longhaul run uses two processes.
* One process hosts the device SDK.  The other process hosts the service SDK.
* These processes are paired at launch time.
* In production, they run in two separate docker containers.
* The language of the device process and the langauge of the service process are independent.
* The only communication between the processes is through IoTHub.
* You cannot reasonably test the device client without using the service client and vise-versa, so both are required.
* The communication to iothub is well defined, and all languages follow the same schema.  This communication schema includes back-and-forth oprations, such as the device sending telemetry to the service, and the service using c2d to acknowledge the telemetry.
* Each process sends a "heartbeat" to the other process, using telemetry and c2d.  If a process doesn't receive a heartbeat from it's peer, it fails.
* The processes can both be configured via desired properties.  This schema is also well-defined and cross-langague.
* The longhaul test runs until failure.  When the test starts, two new container instances are started.  Each run of the test uses a new device instance.  When the test completes, the containers stop running.
* Right now, container and device cleanup is manual.
* Some performance telemetry goes to azure monitor, and some goes to iothub.  This may be narrowed down to one destination later.
