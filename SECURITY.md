# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
responsibly. **Do not open a public GitHub issue.**

Email the maintainer directly with details of the vulnerability. Include:

- A clear description of the issue
- Steps to reproduce
- Affected versions
- Any potential mitigations you have identified

You will receive a response within 72 hours. Once the vulnerability is
confirmed and a fix is released, public disclosure is welcome.

## Security Considerations for Deployments

This project handles real-time vehicle telemetry and driver data. When
deploying, ensure:

1. **Environment files** (`.env`) are never committed to version control
   and contain no hard-coded secrets. Use the `.env.example` template.

2. **API keys** (Gemini, Telegram) are rotated regularly and stored
   exclusively in environment variables.

3. **PostgreSQL connections** use TLS. Railway provides this by default
   for `*.railway.internal` addresses.

4. **MQTT connections** to HiveMQ use TLS on port 8883 in production.

5. **CORS** is configured restrictively — the Express server should only
   accept requests from trusted origins.

6. **Rate limiting** is applied to the `/api/ai/analyze` endpoint to
   prevent API key abuse.
