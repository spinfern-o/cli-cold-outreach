# CompanyScout privacy policy

CompanyScout is a personal command-line tool run by its owner on their own computer, for their own internship outreach. It is not offered to anyone else.

**Gmail access.** With the owner's consent (Google OAuth), it uses:
- `gmail.compose` to create drafts and send emails the owner has individually approved;
- `gmail.readonly` to read replies, bounces and opt-outs on threads it started, and to check the Sent folder so the same person is not emailed twice.

**Data.** Everything stays on the owner's Mac: a local SQLite database and an OAuth token in the macOS Keychain. Nothing is sold or shared, and no data is sent to any server operated by this project. Draft text and public company research are processed by Anthropic's Claude API to write and check emails.

**Opt-out.** Anyone who replies asking not to be contacted is added to a permanent do-not-email list.

**Revoking access.** `outreach gmail disconnect` revokes the token, or remove access at https://myaccount.google.com/permissions.

Contact: through the support email listed on the app's Google consent screen.
