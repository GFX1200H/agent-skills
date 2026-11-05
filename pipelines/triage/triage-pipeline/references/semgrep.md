Semgrep
semgrep is an extensible static analysis (SAST) tool that searches code, finds bugs, and enforces secure guardrails and coding standards.
Inputs
   Input    Description    Default   fine_grained_exclusionsList of specific file + rule combinations to exclude with justification (preferred).[]exclusionsThe semgrep rules to ignore from ALL files (generates warnings).[]ignoreFiles that semgrep should ignore for ALL rules.[]scan_toleranceSeverity threshold: WARNING (default) or ERROR.WARNING  
Examples
Fine-Grained Exclusions (Preferred)
Fine-grained exclusions allow you to exclude specific file + rule combinations with a required justification. This provides better security posture and faster UPISEC review compared to broad exclusions.
name:
  semgrep:
    fine_grained_exclusions:
      # Exclude a specific rule only in a specific file
      - rule: "app.rules.community.dockerfile.security.secret-in-build-arg"
        file: "Dockerfile"
        justification: >-
          CI_JOB_TOKEN is used in buildargs for kaniko to push to the registry.
          This token expires when the job finishes, so leakage is not a concern.
      
      # Use glob patterns to match multiple files
      - rule: "app.rules.community.go.lang.security.audit.net.use-tls"
        file: "backend/src/*.go"
        justification: "Istio sidecar handles TLS termination. HTTP is used internally for Istio mTLS compatibility."
      
      # Multiple exclusions for different scenarios
      - rule: "app.rules.community.python.lang.security.audit.insecure-hash-function"
        file: "tests/test_*.py"
        justification: "MD5 is used only for test fixtures, not for security-sensitive operations."
Benefits:

Granular control over what is excluded
Required justifications improve audit trail
Faster UPISEC security reviews
Clear documentation of why exceptions exist

Validation:
If a fine-grained exclusion doesn't match any findings, a warning will be generated to help catch typos in file paths or rule IDs. For example:
[WARNING] Fine-grained exclusion had no matches - Rule: 'app.rules.community.go.lang.security.audit.net.use-tls', File: 'backend/src/mian.go'. Check for typos in the file path or rule ID.
Excluding semgrep rules from evaluation during scans (Deprecated)
Warning: Broad rule exclusions exclude a rule from ALL files and will generate warnings in the pipeline. Consider using fine_grained_exclusions instead.
To exclude rules from failing semgrep scan, specify them as a list in the <project>:semgrep:exclusions field.
name:
  semgrep:
    exclusions:
      - app.rules.community.dockerfile.security.secret-in-build-arg
      - app.rules.community.bash.lang.best-practice.useless-cat
Reducing Scan Sensitivity
The scan sensitivity is at default WARNING, this may be too sensitive for certain applications due to large logs or long job duration.
To reduce the scan sensitivity, set a parameter in the cdso_config.yml of a project under the semgrep section with the 'scan_tolerance' keyword. 'scan_tolerance' can be set to "ERROR" as seen below for the semgrep job to reduce the sensitivity.
name:
  semgrep:
     scan_tolerance: "ERROR"
Ignoring files in semgrep scans
To ignore files during semgrep scan, specify the filepaths of the files as a list in the <project>:semgrep:ignore field.
name:
  semgrep:
    ignore:
      # Ignore the specific file at the relative or absolute path provided.
      - "path/to/specific/file.txt"
      # Ignore any file or directory named "foo" anywhere.      
      - "**/foo"
      # Ignore any files or subdirectories in ./foo.
      - foo/**
      # Ignore any files in foo ending in *.py
      - foo/*.py
      # Ignore any file in foo or any of its subdirectories starting with
      # "ignored"
      - foo/**/ignored_*
The .semgrepignore syntax is similar to the .gitignore syntax.
Configuring Scan Timeout in CI Pipeline (.gitlab-ci.yml)
For large codebases, mono-repos, or repositories with extensive file trees, the default 1-hour SAST scan timeout may be insufficient. You can increase the Semgrep scan job timeout up to 4 hours by specifying semgrep_job_timeout in your .gitlab-ci.yml review component configuration.
include:
  - component: code.cdso.army.mil/cdso/cdso/review@main
    inputs:
      name: my-app
      pipeline_type: application-container
      semgrep_job_timeout: "2 hours"
Allowed Values
The semgrep_job_timeout input accepts durations between 1 and 4 hours:
   Value    Description   "1 hour" / "1 hr" / "1hr"Default duration"2 hours" / "2 hr" / "2hr"Extended duration for medium-to-large projects"3 hours" / "3 hr" / "3hr"Extended duration for large multi-package projects"4 hours" / "4 hr" / "4hr"Maximum allowed duration under platform policy  

Note: Values are validated against ^([1-4]) ?(hr|hour|hours)$. Values exceeding 4 hours or specified in minute/second increments (e.g., "90 minutes", "30m") will fail CI component validation.

Policy Alignment
cDSO prefers semgrep over alternatives like sonarqube and fortify for a few primary reasons, summarized below from our whitepaper:

Open-source: semgrep's licenses much more of its open-source/"community" product under LGPL-2.1 than sonarqube's open-source offering, while fortify has no open-source offering. Following our own product philosophy, cDSO prioritizes open-source, freely available tools over paid tools when tools' capabilities are equivalent or similar.
Performance: As noted in our whitepaper, semgrep's analysis engine is more performant, has higher recall, and is significantly (p<0.05p < 0.05p<0.05) more accurate at the domain task in repositories tested than both sonarqube and fortify.
Extensibility: semgrep's community rulesets are comprehensive for the languages most of our users are already using. In contrast to "prepackaged" rulesets provided by the alternatives considered, semgrep provides simple interfaces to extend rulesets as needed.
