# Zap

The [zap](https://code.cdso.army.mil/cdso/containers/tool/zap) tool performs DAST scans against a containers ports using [NMAP](https://nmap.org/) and [OWASP Zed Attack Proxy (ZAP)](https://www.zaproxy.org).

## Dockerfile Requirements

The Dockerfile must include an EXPOSE statement for each port for each service exposed by the container. If these are missing, it will result in a failure.

The Dockerfile should include either CMD or ENTRYPOINT directives in the final stage. If no CMD or ENTRYPOINT directive is provided, it will produce a warning.

## Configuration

The zap configuration must be set in the [cDSO Config](https://pages.cdso.army.mil/cdso/documentation/reference/cdso-config/) file.

**Example Zap Configuration**

The following represents a minimal zap configuration defined in `cdso_config.yml`:



```yaml title="cdso_config.yml"
mycontainer: # The container name
  connection_context: external # Options: external | internal | none
  container_lifespan: persistent # Options: multiple | ephemeral | persistent
```

For basic applications assuming no mitigations are required, and no companion services or databases are needed, no zap configuration is needed. Leaving it out applies a default config. However, `connection_context` and `container_lifespan` must be defined for containers. See [cDSO Container Config](https://pages.cdso.army.mil/cdso/documentation/reference/cdso-config/#pipeline-specific-configuration-options) reference for more information.

```yaml title="cdso_config.yml"
mycontainer: # The container name
  zap:
    port: 12345 # Tell zap to bind to port 12345 to perform scans
```

- **`port`**: specifies the port zap will bind to when performing scans. The default is `65534`. If the container uses port `65534`, use this setting to define an alternate port for zap.

```yaml title="cdso_config.yml"
mycontainer: # The container name
  zap:
    services_ports: [5432] # Ignore port 5432, your application should not expose or serve anything at this port.
```

- **`service_ports`**: A list of integers/ports to ignore. For example, developers can use [Gitlab Services](#gitlab-services-enabling-zap-scan) to assist the container image being scanned to run. A common dependency is a database. In this example, a PostGres database is used. In this case, the port being used is 5432 which can be ignored since the port does not belong to the container image being scanned.

```yaml title="cdso_config.yml"
mycontainer:
  zap:
    cert_path: "path/to/cert"
```

- **`cert_path`**: Provides additional certificate(s) to trust. For example, if the container uses a self-signed HTTPS certificate to communicate.

```yaml title="cdso_config.yml"
mycontainer:
  zap:
    mitigations:
      ZAP_10021: >-
        A valid justification for ignoreing zap rule ZAP-10021
```

- **`mitigations`**: Defines a list of zap rules to ignore and a reason to justify excluding the rule.



## Gitlab Services enabling Zap Scan

The `zap` job will scan the conainer image as a [Gitlab Service](https://docs.gitlab.com/ci/services/) with a DNS alias of `app-server`.

Some images require a database to connect to in order to startup.

**Important**: The image to be scanned must be able to startup, otherwise the scan job will fail. Below is an example `cdso_config.yml` configuration (left) to add a Postgres database. The image on the right shows an approximation of what cDSO will convert the configuration to. By default, only the `app-server` Gitlab Service will run.

![gitlab services config](readme/images/zap_config.png)

- The <span style="color: orange;">ORANGE</span> box `scanned_image_variables` defines the variables that will be passed to `app-server` which is the alias for the image being scanned.

```yaml title="cdso_config.yml"
mycontainer:
  zap:
    scanned_image_variables:
      CSRF_AUTH_KEY: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      DB_HOST: db
      DB_USER: $DATABASE_USER
      DB_PASS: $DATABASE_PASSWORD
      DB_DB: postgres
```

- The <span style="color: red;">RED</span> box `gitlab_services` array defines the Gitlab Service(s) that will run. In this example, a Postgres database container will run.

```yaml title="cdso_config.yml"
mycontainer:
  zap:
    gitlab_services:
      - name: my_service_container_image_url
        alias: dns_alias
```

- The <span style="color: green;">GREEN</span> box `database_init` optionally defines the connection information to use and the SQL script file to execute once connected to the database.

```yaml title="cdso_config.yml"
mycontainer:
  zap:
    database_init:
      type: postgres # Currently the only database type supported
      host: dns_alias_goes_here
      user: $DATABASE_USER
      password: $DATABASE_PASSWORD
      init_file_path: path_to_sql_script_file
```

- The <span style="color: purple;">PURPLE</span> box `wait` defines the amount of time to wait before scanning `app-server`. This is useful if the app has a time consuming initialization script that it runs to bootstrap.

```yaml title="cdso_config.yml"
mycontainer:
  zap:
    wait: 10 # seconds to wait before scanning contianer image
```

## Skipping Zap

If the container exposes no ports and does not need a zap scan, developers can set the skip reason to skip the job. This is useful if the security group configuration requires zap scans for your organizations application containers, but a particular container does not expose ports, a skip reason can be provided for that application to avoid job failure.

```yaml title="cdso_config.yml"
mycontainer:
  connection_context: NONE
  zap:
    skip_reason: "The container exposes no ports for inbound connections"
```

**ImportanT:** If a `zap_skip` reason is provided, ensure there are no `EXPOSE` statements in the container's `Dockerfile`.

## Debug

Debug logs can be set globaly in the `cdso_config.yml` file. This enables `CI_DEBUG_SERVICES` which is a Gitlab [predifined variable](https://docs.gitlab.com/ci/variables/predefined_variables/). Enabling this allows developers to see Gitlab Service(s) logs to determine why a container image is not being scanned. It also enables the zap tool's application debug logging for more verbose output including the nmap and zap scan output streams.

Note: In previous versions, these were controlled separately by the gloabal `debug` setting and the `mycontainer:zap:debug` setting. This has been consolidated under the global `debug` setting. The `mycontainer:zap:debug` option is deprecated.

```yaml title="cdso_config.yml"
debug: true # enables Gitlab Services Logs
mycontainer:
```

### Viewing Zap Debug Logs

Developers may see "The current user is not authorized to access the job log" as shown below. In this case, the developer does not have `developer` or higher access on the `${CDSO_BASE}/cdso` project. Gitlab requires users to have `developer` or higher access on the project when `CI_DEBUG_SERVICES` is enabled. Continue reading for how to view the debug log when access is denied.

![zap_access_denied](readme/images/zap_access_denied.png)

When `zap` logs are not available because of permissions issues, go to the `review` job as shown below:

![zap_review](readme/images/zap_review.png)

The `review` job will containe the log as a Gitlab Artifact to download and is output in the `after_script` (see next section for more details). This will **not** be the case if the project access level is set to `private` because the job log could reveal implementation details.

### Interpreting Zap Debug Logs

In the `review` job, find "Showing maximum of last 20 lines". Beneath this is up to 20 lines of the job log. See <span style="color: yellow;">YELLOW</span> box below for example output. Developers can also download the entire job log by clicking the `BROWSE` button in the <span style="color: red;">RED</span> box shown below.

![zap_log](readme/images/zap_log.png)

Notice this line the the screenshot above.

```text
WARNING: failed to open log stream for container app-server: container "app-server terminated with non-zero status: 128
```

This is the most common reason `zap` fails to find an open port. The `app-server` which is the DNS/hostname entry for the container image being scanned never started. Therefore it could not expose a port to be scanned.

The second line states:

```text
unable to start container process: error during container init: exec: "/wikiapp": stat /wikiapp: no such file or directory
```

The `Dockerfile`'s entrypoint was deliberately borken for demonstration purposes to show this error. `/wikiapp` does not exist in the contianers `$PATH`, so it failed to start.

### Debugging Locally

It is common that the job log will not reveal the root cause of why it exited with a non-zero status. In such cases, the developer should run the container locally and debug it's startup issues.

The <span style="color: cyan;">BLUE</span> box in the screenshot above shows the full URI to the container image that was scanned. Copy this value from the log and pull the image to debug locally.

### Failure examples
### 1. Connection Context None
**Job Log**:

![Connection Context None](./readme/images/1_conn_ctx_none.png)

This is a validation error. The `cdso_config:mycontainer:connection_context` is `none` but the `Dockerfile` includes `EXPOSE` directives.

**Problem**: 

`Dockerfile`
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 8080
CMD ["runsomething"]
```

`cdso_config.yml`
```yml
mycontainer:
  connection_context: none
```

**Solution A**:
If this container does not actually expose any services, remove the `EXPOSE` directive from the `Dockerfile` and add a `zap:skip_reason` to skip the zap scan.

`Dockerfile`
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
CMD ["runsomething"]
```
`cdso_config.yml`
```yml
mycontainer:
  connection_context: none
  zap:
    skip_reason: "A valid reason to skip the zap job"
```

**Solution B**:
If this container does actually expose services, change the `connection_context` to the appropriate value.

`cdso_config.yml`
```yml
mycontainer:
  connection_context: external
```

### 2. Detected ports not EXPOSE'd in Dockerfle
**Job Log**:

![Detected Exposed Mismatch](./readme/images/2_detected_exposed_mismatch.png)

This indicates a problem with the `Dockerfile`. In this example, the container provides 6 different services on 6 different ports, but the `Dockerfile` does not include an `EXPOSE` directive for them.

**Problem**: 

`Dockerfile`
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
CMD ["runsomething"]
```


**Solution**:
Add an `EXPOSE` directive for each port exposed by the container.

`Dockerfile`
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 8080
EXPOSE 5
EXPOSE 8081
EXPOSE 35000
EXPOSE 5000
EXPOSE 9001
CMD ["runsomething"]
```

### 3. zap:service_port EXPOSE'd in Dockerfile
**Job Log**:

![Service Port Expose Overlap](./readme/images/3_zap_service_expose_overlap.png)

This indicates a port defined in `zap:service_ports` is also defined in an `EXPOSE` directive which is a conflict.

**Problem**: 

`Dockerfile` for container1
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 8080
CMD ["runsomething"]
```

`Dockerfile` for container2
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 8080
CMD ["runsomething"]
```

`cdso_config.yml`
```yml
container1:
  zap:
    service_ports: [8080]
    gitlab_services:
      - name: registry.cdso.army.mil/somegroup/someproject/container2:sometag
        alias: container2

container2:
  zap:
    service_ports: [8080]
    gitlab_services:
      - name: registry.cdso.army.mil/somegroup/someproject/container1:sometag
      - alias: container1
```

**Solution A**:
If this container and a companion container serve something over the same port, one needs to change. If there is no companion service container, no `zap:gitlab_services` are needed, then remove `zap:service_ports` as it is not needed.

`Dockerfile` for container1
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 8080
CMD ["runsomething"]
```

`Dockerfile` for container2
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 9090
CMD ["runsomething"]
```

`cdso_config.yml`
```yml
container1:
  zap:
    service_ports: [9090] # Ignore port 9090 exposed by container2
    gitlab_services:
      - name: registry.cdso.army.mil/somegroup/someproject/container2:sometag
        alias: container2

container2:
  zap:
    service_ports: [8080] # Ignore port 8080 exposed by container 1
    gitlab_services:
      - name: registry.cdso.army.mil/somegroup/someproject/container1:sometag
      - alias: container1
```

**Solution B:**

If there is no companion service container, no `zap:gitlab_services` are needed, then remove `zap:service_ports` as it is not needed.

`Dockerfile` for container1
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 8080
CMD ["runsomething"]
```

`Dockerfile` for container2
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 8080
CMD ["runsomething"]
```

`cdso_config.yml`
```yml
container1:
  zap:
    # Other configs

container2:
  zap:
    # Other configs
```

### 4. Needs Skip Reason
**Job Log**:

![Needs Skip Reason](./readme/images/4_needs_skip_reason.png)

If the `Dockerfile` contains no `EXPOSE` directives and the `mycontainer:connection_context` is `none`, this assumes there is no application to serve in this container and zap should be skipped since there's nothing to scan. The solution is to add a `zap:skip_reason`.

However, if your application does provide a service, then the services should be EXPOSE'd via the `Dockerfile` and the `connection_context` should be changed accordingly. This example assumes the developer configured their `Dockerfile` and `connection_context` correctly to start with and just needs to add a `zap:skip_reason`.

**Problem**: 

`Dockerfile`
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
CMD ["runsomething"]
```

`cdso_config.yml`
```yml
mycontainer:
  connection_context: none
```

**Solution**:

Add a `zap:skip_reason`

`cdso_config.yml`
```yml
mycontainer:
  connection_context: none
  zap:
    skip_reason: "A valid reason to skip the zap scan"
```

**NOTE:** If your container provides no services, reconsider whether or not it should be classified as an 'application-container'

### 5. Missing EXPOSE Directives
**Job Log**:

![Connection Context None](./readme/images/5_external_no_expose.png)

This is a `Dockerfile` validation error. The `mycontainer:connection_context` is `external` but the `Dockerfile` is missing `EXPOSE` directives.

**Problem**: 

`Dockerfile`
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
CMD ["runsomething"]
```

`cdso_config.yml`
```yml
mycontainer:
  connection_context: external
```

**Solution A**:
If this container does not actually expose any services, change `connection_context` to `none` and a `zap:skip_reason` to skip the zap scan.

`cdso_config.yml`
```yml
mycontainer:
  connection_context: none
  zap:
    skip_reason: "A valid reason to skip the zap job"
```

**Solution B**:
If this container does actually expose services, add an `EXPOSE` directive for each service in the `Dockerfile`.

`Dockerfile`
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 8080
CMD ["runsomething"]
```

### 6. No Services Detected
**Job Log**:

![No Services Detected](./readme/images/6_no_services.png)

Assuming your `Dockerfile` contains `EXPOSE` directives and runs a service on those exposed ports, this error indicates no listening services were detected on those ports. This is commonly caused by application startup latency or an application startup error.

**Solution**: 

Enable `debug` to help identify what's happening and add a `zap:wait` value to delay scans to allow the services to be scanned to finish starting up.

`cdso_config.yml`
```yml
debug: true
mycontainer:
  zap:
    wait: 60 # wait time is in seconds.
```

### 7. No Zappable Targets
**Job Log**:

![No Zappable Targets](./readme/images/7_no_zappable_targets.png)

This error indicates the container exposes some service, but does not expose any http-based services, therefore there is nothing to scan with zap.

**Problem**: 

`Dockerfile`
```Dockerfile
ARG CI_REGISTRY="registry.cdso.army.mil"
FROM ${CI_REGISTRY}/cdso/containers/approved-base/ubuntu:24.04
EXPOSE 35000 # some non http service
CMD ["runsomething"]
```

**Solution**:

Zap can only scan http/https based services. If the container exposes no such service, then add a `zap:skip_reaon` to skip the zap scan.

`cds_config.yml`
```yaml
mycontainer:
  zap:
    skip_reason: "A valid reason to skip the zap scan."
```

### 8. Failure with Zap Findings
**Job Log**:

![Fail with findings](./readme/images/8_fail_with_findings.png)

This error indicates 6 unmitigated zap findings were detected on the exposed services.

**Solution**:

The first step would be to fix the issue via application configuration changes or updates. If this cannot solve the problem, a mitigation statement should be added to `zap:mitigations` for each remaining alert. These are intended to describe what measures are in place that makes the finding inert or is a statement acknowleging the finding exists and accepting the risk. These must be approved by the security reviewers.

`cdso_config.yml`
```yaml
mycontainer:
  zap:
    mitigations:
      ZAP_10021: >-
        Mitigation for 10021
      ZAP_10038-1: >-
        Mitigation for 10038-1
      ZAP_10020-1: >-
        Mitigation for 10020-1
      ZAP_10036-2: >-
        Mitigation for 10036-2
```

### Success Examples

### 1. Pass - With Skip Reason

In this example, a `zap:skip_reason` was configured. This exits early with a success state.

![Pass Skip](./readme/images/9_pass_skip.png)

### 2. Pass - With Mitigated Findings

In this example, 4 services were identified, two of which were http-based (zappable). It included 3 warnings, two of which indicated it is skipping zap scan for the other two non-http based services and that the `Dockerfile` is missing a `CMD` or `ENTRYPOINT` directive. It also indicates zap found 8 findings all of which were mitigated by a mitigation statement in `zap:mitigations`. This results in a `PASS`.

![Pass With Mitigated Findings](./readme/images/10_pass_with_mitigations.png)
