# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x     | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not open a public issue.**
2. **Use GitHub Private Vulnerability Reporting:**
   `Security` -> `Advisories` -> `New draft security advisory`
3. Include a description, reproduction steps, and potential impact.

If private vulnerability reporting is not enabled in this repository,
contact the maintainer through GitHub directly and do not publish details
in a public issue.

## Scope and Threat Model

open-compute drives a computer through an LLM. Treat it as you would any tool
that can move the mouse, type, and launch applications:

- **Run real backends in isolation.** Use a dedicated VM or container, not your
  primary desktop. The library cannot sandbox the host for you.
- **Keep a human in the loop.** The default `SafetyPolicy` mode is `confirm`,
  which blocks risky actions (clicks, typing, key presses, app launches) unless
  a confirmation callback approves them. Do not switch to `allow_all` outside a
  disposable, isolated environment.
- **On-screen content is untrusted input.** A page or app the agent views can
  contain prompt-injection text. The model may be steered by it; the safety gate
  and human confirmation are your defense.
- **Secrets.** Never hard-code API keys. The Claude/OpenAI backends read keys
  from the environment via their SDKs. Do not log screenshots or transcripts
  that may contain credentials.

## Response Time

For smaller solo projects, response times may vary. Critical issues will be
prioritised. Please allow reasonable time before public disclosure.
