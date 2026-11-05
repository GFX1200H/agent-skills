Trufflehog Scanner
1. Overview
This tool is a containerized trufflehog scanner designed to be used as a job image in a CI/CD pipeline. It scans a Git repository for secrets, filters the results, and produces a JSON report that can be used to pass or fail the pipeline.
2. Usage in GitLab CI
To use this scanner, specify it as the image for a job in your .gitlab-ci.yml file. Then, simply call the execute command in the script section.
Example .gitlab-ci.yml Job:
secret-scan:
  stage: scans
  image: cdso/containers/tool/trufflehog:latest
  variables:
    CDSO_CONFIG_PATH: "cdso_config.yml"
    NAME: "app"
  script:
    - execute
  artifacts:
    when: always
    paths:
      - trufflehog.json
      - trace.json
3. Configuration
The scanner's behavior is controlled by CI/CD variables (or env variables) and a YAML configuration file in your repository.
3.1. CI/CD Variables
Set these in the variables section of your CI job:
CDSO_CONFIG_PATH: (Required) Path to your configuration file within the repository. Defaults to cdso_config.yml.
NAME: A name for the scan instance, used as a key to look up configuration in your cdso_config.yml. Defaults to trufflehog.
3.2. cdso_config.yml
This file must exist in your repository. It allows you to filter the scanner's findings by providing lists of false positives and file paths to exclude.
Example cdso_config.yml:
# cdso_config.yml
application_name:
  trufflehog:
    # A list of strings that, if matched in a finding, will be ignored.
    # These are treated as REGEX patterns.
    false_positives:
      - "example-key-for-testing"
      - "BEGIN RSA PRIVATE KEY"

    # A list of file paths to exclude from the scan.
    # These are treated as REGEX patterns.
    excluded_files:
      - "*/tests/fixtures/*"
      - "docs/example.go"
# cdso_config.yml

application_name:
  trufflehog:
    false_positives: ["verbatum-secret"] 
    excluded_files: ["with_secrets.txt", "ignore/all/path/*"]
4. Outputs
The container produces the following files, which should be saved using the artifacts keyword in your CI job:
trufflehog.json: The primary machine-readable output.

status: "PASS" or "FAIL". The job will fail if the status is "FAIL".
data: An array of secrets found after filtering.
ignored: An array of findings that were ignored.

trace.json: A log file from trufflehog containing diagnostic information.
5. Debugging
If the job fails, first check the job logs. Then, download the build artifacts and inspect trufflehog.json to see which secrets were detected.
Maintainers
