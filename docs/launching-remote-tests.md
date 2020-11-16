# Launching remote tests

These tests can run remotely inside of Docker containers using Azure Container Instances.

To understand this, it may help to read about how the runtime environment is organized [here](./runtime.md)

In order to launch the app remotely, you need to:
1. Build docker images for the device app and the service app
2. Launch at least one service app with some unique `servicePool` name
3. Launch at least one device app requesting to pair with that `servicePool`

## Building docker images

First, make sure your environment is set up.  Instructions are [here](./setting-up-your-thief-environment.md)

Next, build the service image.  It requires a `source` and a `library_version`.
The `source` defines "where to get the code"
and the `library_version` defines "which version of the code to get from that source".

The `source` is selected from a set of valid values, based on the langauge you are building.
The format of the `library_version` is based on the selected `source`.

We currently support the following sources:

| langauge | source | language default? | library_version format |
| - | - | - | - |
| Python | pypi | yes |  package version.  e.g. "2.4.0" |

The libraries are built using `scripts/build_image.sh`.

```
USAGE: ./build-image.sh [--platform platform] --langauge language_short_name --library library [--source library_source] --version library_version [--tag extra_tag]
```

`platform` can be either `linux` or `windows` with `linux` as the default
_(note: windows support is only partially implemented)_

valid language_short_name values are:
| language_short_name | meaning |
| - | - |
| py27 | Python 2.7 |
| py35 | Python 3.5 |
| py36 | Python 3.6 |
| py37 | Python 3.7 |
| py38 | Python 3.8 |

`library` can be either `device` or `service`

`extra_tag` is an extra tag that can be added to the docker image for added when launching tests.
This was added to reduce the change that a typo would cause you to run the wrong code in a container.
For example,
* if you build an image for `py27 device 2.4.0` without a tag and then accidentally tried to run an image for `py37 device 2.4.0`, the launch script may not notice that you built for `py27` and launched for `py37`.  (unless there wasn't a `py37` image available).
* However, if you built an image for `py27 device 2.4.0` with the tag `nov14-lts`, and the accidentally tried to run an image for `py37 device 2.4.0` with the same tag, the launch script would more easily detect that you tired to launch for `py37` because there is probably not a `py37` inmage with the `nov14-lts` tag,

Example to build the service image:
```
./build-image.sh --language py37 --library service --version 2.2.3 --tag nov12
```

Example to build the device image:

```
./build-image.sh --language py37 --library device --version 2.4.0 --tag nov12
```

## Launching tests

To launch tests, you will typically launch one service app and several device apps.
In the future, service apps may be longer running and it may not be necessary to launch one each time.

Containers are launched using `scripts/run_container.sh`

Most of the parameters are the same as `build_image.sh` above, with the addition of `--device_id` and `--pool` options.

```
USAGE: ./scripts/run-container.sh [--platform platform] --langauge language_short_name --library library [--source library_source] --version library_version --pool service_pool [--device_id device_id] [--tag extra_tag]
```

* `--pool` is required for both `service` and `device` containers.
* `--device_id` is required only for `device` containers.

Because the current tests use DPS to provision devices, it is not necessary to manually add the devices to the hub registry before launching the tests.

Example to launch a service container:
```
scripts/run-container.sh --language py37 --library service --version 2.2.3 --tag nov12 --pool nov12
```

Note: The tag name (`nov12`) is for tagging the docker iamge.
The pool name (also `nov12`) is used to match device and service apps.
The fact that these strings are the same is coincidental.
There is no relationship between these values.

Example to launch a device container:
```
scripts/run-container.sh --language py37 --library device --version 2.4.0 --tag nov12 --pool nov12 --device_id nov12-01
```


