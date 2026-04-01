"""
Generate OAuth2 refresh token for sen-ai-data Google Cloud project.
Connect with data@sen-ai.fr to authorize all Google APIs.

Usage:
    pip install google-auth-oauthlib
    python scripts/generate_oauth_token.py
"""

from google_auth_oauthlib.flow import InstalledAppFlow

# Replace with your OAuth credentials from Google Cloud Console (sen-ai-data project)
CLIENT_ID = "REPLACE_WITH_YOUR_CLIENT_ID"
CLIENT_SECRET = "REPLACE_WITH_YOUR_CLIENT_SECRET"

SCOPES = [
    "https://www.googleapis.com/auth/adwords",              # Google Ads
    "https://www.googleapis.com/auth/analytics.readonly",    # GA4
    "https://www.googleapis.com/auth/business.manage",       # GBP
    "https://www.googleapis.com/auth/drive",                 # Google Drive
    "https://www.googleapis.com/auth/spreadsheets",          # Google Sheets
    "https://www.googleapis.com/auth/forms.body",            # Google Forms
    "https://www.googleapis.com/auth/webmasters.readonly",   # Search Console
    "https://www.googleapis.com/auth/content",               # Google Content API
]

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    },
    scopes=SCOPES,
)

creds = flow.run_local_server(port=9090, open_browser=True)

print("\n" + "=" * 50)
print("OAUTH TOKEN GENERATED SUCCESSFULLY")
print("=" * 50)
print(f"Refresh token:  {creds.refresh_token}")
print(f"Access token:   {creds.token}")
print(f"Client ID:      {creds.client_id}")
print(f"Client Secret:  {creds.client_secret}")
print("=" * 50)
print("\nCopy the refresh token into your .env files and GitHub Secrets.")
print("This refresh token works for ALL the scopes listed above.")
