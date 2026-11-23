# SearchFlow public access

The public workbench is available at `/app` when Google sign-in is configured. Visitors can open the page and choose a language. Signed-in users can submit searches, track their own jobs, and download their own results. The existing root workbench and operator endpoints retain the site access gate.

Each Google subject maps to a personal workspace with no API key. The web session lasts seven days. The account accepts five new tasks per UTC day, up to 30 successful searches per UTC month, and one queued or running task across Maps and search. Failed or cancelled tasks release the monthly reservation and still count toward the daily limit. Reusing the same idempotency key for the same request returns the existing job. Existing provider-level and platform queue limits continue to apply.

## Deployment inputs

1. Choose an HTTPS hostname on a domain your organization can verify in Google Search Console. Publish the public homepage, privacy policy and terms on the verified domain.
2. Create a Google OAuth **Web application** client with an **External** audience. Register the exact redirect URI `https://<public-host>/api/account/google/callback`. Request only the `openid` scope. Use the Google console's release process for public availability.
3. Mount a server-only JSON file with restricted permissions in the backend web service. Set `SEARCHFLOW_GOOGLE_LOGIN_CONFIG` to its path inside that service. Use these keys:

   | Key | Value |
   | --- | --- |
   | `origin` | `https://<public-host>`, without a path or trailing slash |
   | `client_id` | OAuth Web client ID |
   | `client_secret` | OAuth Web client secret |
   | `privacy_url` | Public HTTPS privacy policy |
   | `terms_url` | Public HTTPS terms |

## Release and verify

1. Prepare a versioned backend revision containing `self_service.py`, `public_workbench.py`, `product_http.py`, `quick_request_http.py`, `google_local_http.py`, `google_local_jobs.py` and the two `assets/public-workbench.*` files. Apply only the reviewed changes to the active frozen backend.
2. Run `python -m realtime.self_service --initialize` once using the backend web service's database settings. It adds the identity and session tables to the existing product schema.
3. Route `/app`, `/app.js`, `/app.css` and `/api/account/` using the SearchFlow gateway template. On the verified public hostname, redirect `/` to `/app`. Keep the existing private operator hostname and access rules. At each ingress, omit OAuth callback query strings from access logs.
4. Verify the release in a browser with two test accounts. Confirm visitor access to `/app`, Google sign-in, independent usage, job creation, results, download, cross-account denial, and sign-out. Verify the daily limit and verify that the existing private operator routes still require their site credentials.
5. To remove public access, unset `SEARCHFLOW_GOOGLE_LOGIN_CONFIG`, restart only the backend web service, and withdraw the public routes. The separate account and session tables can remain for rollback.

Official references: [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect) and [Google OAuth production readiness](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).
