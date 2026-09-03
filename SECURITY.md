# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

We take the security of Taurus Supervisor seriously. If you believe you have found a security vulnerability, please report it to us as described below.

### Reporting Process

1. **DO NOT** create a public GitHub issue for security vulnerabilities
2. Email your findings to: **taurus-stackoutlook.com**
3. Include the following information:
   - Description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact assessment
   - Suggested fix (if any)

### Response Timeline

- **Initial Response**: We will acknowledge receipt of your vulnerability report within 48 hours
- **Assessment**: We will assess the vulnerability and provide an initial response within 7 days
- **Fix Development**: We aim to develop and release a fix within 30 days for critical vulnerabilities
- **Disclosure**: We will coordinate with you on the disclosure timeline

### Security Best Practices

When deploying Taurus Supervisor in production:

1. **Always use TLS/mTLS** for communication between Supervisor and Server
2. **Keep certificates secure** and rotate them regularly
3. **Use strong request signing keys** and rotate them periodically
4. **Restrict file permissions** on configuration files (600) and binaries (755)
5. **Monitor logs** for suspicious activity
6. **Keep the system updated** with the latest security patches
7. **Use firewall rules** to restrict access to only necessary ports
8. **Run with least privilege** - avoid running as root unless necessary

### Security Features

Taurus Supervisor includes several security features:

- **mTLS Authentication**: Mutual TLS ensures both client and server verify each other's identity
- **Request Signing**: HMAC-SHA256 signatures prevent request tampering and replay attacks
- **Certificate Revocation**: Immediate response to certificate revocation commands
- **Encrypted Configuration**: Support for Fernet encryption of sensitive configuration values
- **Audit Logging**: Complete audit trail of all operations
- **Zero Port Exposure**: Supervisor does not expose any external ports

### Known Limitations

1. Configuration encryption uses Fernet symmetric encryption, which requires secure key management
2. Certificate auto-renewal requires network connectivity to the Server
3. Request signing is optional and should be enabled in production environments