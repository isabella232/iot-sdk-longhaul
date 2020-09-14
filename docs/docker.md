# image naming

Container source names and images follow the following conventions:

`{langauge}_{os}_{module}_{source}_{version}`

* `language` can be generic (python) where appropriate, and more specific (py37) where appropriate
* `os` is either `linux` or `windows`, though it might be more specific, such as `ubuntu1804`
* `module` can be one of `base`, `device`, and `service`, where `device` and `common` build on `base` and the `base` image is two things:
    * the container definition which is common between device and service.
    * the container definition which is unchanging or rarely changes.  This is typically the base OS plus tools plus dependencies.
* `source` is the source of the client SDK.  For python, it would be `pypi` or `git`.  For C#, it would be `nuget` or `git`.  For node, it would `npm` or `git`
* `version` is the version of the client SDK.  for `git`, it would be in the form 'branch'sha` with slashes converted to underscores.  For other sources (`pypi`, `nuget`, or `npm`), it would be the library version.

Not all fields are necessary in all cases.

dockerfiles:
* `language` is probably generic (Python instead of py37)
* for `base` Dockerfiles, source and version are excluded.  e.g. `Dockerfile.python.linux.base`
* for `device` and `service` Dockerfiles, version is excluded.  e.g. `Dockerfile.python.linux.device.pypi` and `Dockerfile.python.linux.device.git`

base image tags:
* `language` is specific
* `os` might be generic or specific.
* e.g. `py37-windows-base`


device and service image tags:
* all fields are required
* `language` is specific
* `os` might be generic or specific.
* e.g. `py27-linux-device-pypi-2.4.2`

scripts:
* Build scripts follow the same convention as dockerfile, e.g. `build-python-linux-base.sh` or `build-python-linux-device-pypi.sh`



