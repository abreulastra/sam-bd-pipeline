"""
Generate a new Gmail refresh token for sam-bd-pipeline.
 
Usage:
    pip install google-auth-oauthlib
    export GMAIL_CLIENT_ID="..."        # same values as your GitHub secrets
    export GMAIL_CLIENT_SECRET="..."
    python get_refresh_token.py
 
A browser window opens — sign in with the Gmail account the pipeline reads
(the one receiving Devex/DevelopmentAid alerts) and approve access.
The new refresh token is printed at the end.
"""
import os
 
from google_auth_oauthlib.flow import InstalledAppFlow
 
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
 
client_id = os.environ["GMAIL_CLIENT_ID"]
client_secret = os.environ["GMAIL_CLIENT_SECRET"]
 
flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    },
    SCOPES,
)
 
# access_type=offline + prompt=consent forces Google to issue a NEW refresh token
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
 
print("\n=== NEW REFRESH TOKEN ===")
print(creds.refresh_token)
print("\nUpdate the GitHub secret with:")
print("  gh secret set GMAIL_REFRESH_TOKEN -R abreulastra/sam-bd-pipeline")
print("(paste the token when prompted, or use the repo Settings > Secrets UI)")
