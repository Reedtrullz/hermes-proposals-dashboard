# Security Policy

## Reporting A Vulnerability

Please do not open a public issue for authentication bypasses, secret exposure, code execution, or other exploitable security flaws. Report security concerns privately to the repository owner through GitHub's private communication channel or an agreed direct contact, including reproduction steps and affected configuration.

## Deployment Security Notes

- Set `HERMES_REQUIRE_AUTH=1` on any hosted deployment.
- Replace the example `HERMES_API_KEY` with a long random secret and keep it outside source control.
- Set `AUTH_URL` to the trusted authentication host used by the deployed reverse proxy.
- Treat `$HERMES_HOME/proposals.db` and trigger files as operational data. Limit filesystem access and back them up appropriately.
- Do not expose local development servers configured with `HERMES_REQUIRE_AUTH=0`.
- Store the Ansible vault password only in the ignored `.ansible-vault-pass` file or another protected secret mechanism.

## Execution Boundary

This dashboard does not itself execute paid model calls, but it can signal external workers through trigger files. Worker operators must constrain credentials, workspaces, CLI permissions, and approval behavior separately. Proposals routed to powerful executors should receive human review before execution.
