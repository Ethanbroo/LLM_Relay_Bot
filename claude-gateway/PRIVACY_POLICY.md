# Privacy Policy

**Claude Gateway**
Last updated: February 26, 2026

## Overview

Claude Gateway ("the App") is an independent mobile application that provides access to Anthropic's Claude AI through a personal gateway server. This privacy policy describes how the App collects, uses, and protects your information.

Claude Gateway is not affiliated with, endorsed by, or officially connected to Anthropic, PBC.

## Information We Collect

### Personal Mode
When using Claude Gateway in Personal Mode, the App connects to a self-hosted gateway server controlled by the user. In this mode:
- **Messages**: Conversation messages are stored on your self-hosted server's local database. Messages are not stored on any third-party servers operated by us.
- **API Keys**: Your Anthropic API key is stored as an environment variable on your own server. It is never transmitted to or stored by the App developer.
- **Usage Data**: Token usage counts and conversation metadata (titles, timestamps, model used) are stored locally on your server for your personal dashboard.

### Community BYOK (Bring Your Own Key) Mode
When using Claude Gateway in Community BYOK Mode:
- **API Keys**: Your Anthropic API key is encrypted using AES-256-GCM encryption before being stored on the gateway server. The key is decrypted only at the moment of making an API request to Anthropic and is never logged, cached, or transmitted elsewhere.
- **Access Token**: A unique UUID is generated and provided to you upon registration. This token authenticates your requests but does not contain personally identifiable information.
- **Messages**: Conversation messages are stored in the server database to maintain conversation history. Messages are associated with your anonymous access token, not with any personal identity.
- **Usage Data**: Token usage counts are logged for your usage dashboard. No personally identifiable information is associated with usage logs.

### Information We Do NOT Collect
- We do not collect names, email addresses, phone numbers, or any personal contact information.
- We do not collect device identifiers, advertising IDs, or location data.
- We do not use analytics SDKs, tracking pixels, or third-party analytics services.
- We do not collect or store payment information (donations are processed entirely through third-party services).

## How Your Information Is Used

All collected information is used solely to:
1. Deliver AI-assisted responses by forwarding your messages to Anthropic's API
2. Maintain conversation history so you can continue previous conversations
3. Display usage statistics on your personal dashboard
4. Authenticate your requests to the gateway server

## Data Transmission

- All communication between the App and the gateway server occurs over HTTPS.
- Messages you send are forwarded to Anthropic's Messages API to generate responses. Anthropic's use of this data is governed by [Anthropic's Privacy Policy](https://www.anthropic.com/privacy) and [Terms of Service](https://www.anthropic.com/terms).
- No data is sold, shared with, or transmitted to any other third parties.

## Data Storage and Security

- **Encryption**: BYOK API keys are encrypted at rest using AES-256-GCM with a server-side encryption key.
- **Database**: Conversation data is stored in a server-side database. No sensitive data is stored on the mobile device.
- **Access Control**: All API endpoints that access user data require authentication via Bearer token.
- **Rate Limiting**: The server enforces rate limits to prevent abuse.

## Data Retention and Deletion

- You may delete individual conversations at any time through the App.
- In BYOK mode, you may request complete deletion of your account and all associated data by contacting the server operator.
- Self-hosted Personal Mode users have full control over their data and may delete the database at any time.

## Children's Privacy

Claude Gateway is not directed at children under the age of 13. We do not knowingly collect information from children under 13. If you are a parent or guardian and believe your child has provided information through the App, please contact us so we can delete it.

## Third-Party Services

Claude Gateway relies on the following third-party service:
- **Anthropic Messages API**: Your messages are sent to Anthropic to generate AI responses. Please review [Anthropic's Privacy Policy](https://www.anthropic.com/privacy) for details on how Anthropic handles data sent through their API.

## Changes to This Policy

We may update this privacy policy from time to time. Changes will be reflected by updating the "Last updated" date at the top of this document. Continued use of the App after changes constitutes acceptance of the updated policy.

## Contact

If you have questions about this privacy policy or your data, please contact:

Ethan Brooks
Email: ethanbrooks@me.com

## Open Source

Claude Gateway's source code is available for review, allowing you to verify our data handling practices firsthand.
