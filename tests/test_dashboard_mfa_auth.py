from dashboard.cognito_auth import CognitoAdminAuthenticator
from dashboard.mfa_auth import CognitoMfaBridge


class FakeClient:
    def __init__(self):
        self.mode = "mfa"
        self.last_challenge = None
        self.preferences = []

    def admin_initiate_auth(self, **kwargs):
        if self.mode == "mfa":
            return {"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "mfa-session"}
        return {"AuthenticationResult": {"AccessToken": "access-token"}}

    def admin_respond_to_auth_challenge(self, **kwargs):
        self.last_challenge = kwargs
        return {"AuthenticationResult": {"AccessToken": "access-token"}}

    def get_user(self, **kwargs):
        assert kwargs["AccessToken"] == "access-token"
        return {
            "Username": "tony@example.com",
            "UserMFASettingList": ["SOFTWARE_TOKEN_MFA"],
            "PreferredMfaSetting": "SOFTWARE_TOKEN_MFA",
        }

    def admin_list_groups_for_user(self, **kwargs):
        return {"Groups": [{"GroupName": "HBlink4Admins"}]}

    def associate_software_token(self, **kwargs):
        assert kwargs == {"AccessToken": "access-token"}
        return {"SecretCode": "ABCDEF234567"}

    def verify_software_token(self, **kwargs):
        assert kwargs["AccessToken"] == "access-token"
        assert kwargs["UserCode"] == "123456"
        return {"Status": "SUCCESS"}

    def set_user_mfa_preference(self, **kwargs):
        self.preferences.append(kwargs)
        return {}


def config():
    return {
        "region": "ap-southeast-2",
        "user_pool_id": "ap-southeast-2_example",
        "client_id": "client123",
        "admin_group": "HBlink4Admins",
    }


def test_software_token_mfa_login_challenge_round_trip():
    client = FakeClient()
    bridge = CognitoMfaBridge(CognitoAdminAuthenticator(config(), client=client))

    first = bridge.authenticate("tony@example.com", "password")
    assert first.status == "mfa_required"
    assert first.challenge_token
    assert first.challenge_token != "mfa-session"

    completed = bridge.complete_mfa(first.challenge_token, "123456")
    assert completed.status == "authenticated"
    assert completed.identity.username == "tony@example.com"
    assert completed.access_token == "access-token"
    assert client.last_challenge["ChallengeName"] == "SOFTWARE_TOKEN_MFA"
    assert client.last_challenge["Session"] == "mfa-session"
    assert client.last_challenge["ChallengeResponses"]["SOFTWARE_TOKEN_MFA_CODE"] == "123456"


def test_totp_enrollment_status_enable_and_disable():
    client = FakeClient()
    auth = CognitoAdminAuthenticator(config(), client=client)
    bridge = CognitoMfaBridge(auth)

    status = bridge.mfa_status("access-token")
    assert status["enabled"] is True
    assert status["preferred"] is True

    assert bridge.start_totp_setup("access-token") == "ABCDEF234567"
    bridge.verify_totp_setup("access-token", "123456")
    assert client.preferences[-1] == {
        "SoftwareTokenMfaSettings": {"Enabled": True, "PreferredMfa": True},
        "AccessToken": "access-token",
    }

    bridge.disable_totp("access-token")
    assert client.preferences[-1] == {
        "SoftwareTokenMfaSettings": {"Enabled": False, "PreferredMfa": False},
        "AccessToken": "access-token",
    }
