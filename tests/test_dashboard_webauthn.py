import json

from dashboard.cognito_auth import CognitoAdminAuthenticator
from dashboard.webauthn_auth import CognitoWebAuthnBridge


class FakeClient:
    def __init__(self):
        self.challenge_response = None
        self.completed_registration = None
        self.deleted = None

    def admin_initiate_auth(self, **kwargs):
        assert kwargs["AuthFlow"] == "USER_AUTH"
        assert kwargs["AuthParameters"]["PREFERRED_CHALLENGE"] == "WEB_AUTHN"
        return {
            "ChallengeName": "WEB_AUTHN",
            "ChallengeParameters": {
                "CREDENTIAL_REQUEST_OPTIONS": json.dumps(
                    {
                        "challenge": "AQID",
                        "rpId": "dmr.vk2ale.com",
                        "allowCredentials": [{"type": "public-key", "id": "BAUG"}],
                        "userVerification": "required",
                    }
                )
            },
            "Session": "webauthn-session",
        }

    def admin_respond_to_auth_challenge(self, **kwargs):
        self.challenge_response = kwargs
        return {"AuthenticationResult": {"AccessToken": "access-token"}}

    def get_user(self, **kwargs):
        return {"Username": "tony@example.com"}

    def admin_list_groups_for_user(self, **kwargs):
        return {"Groups": [{"GroupName": "HBlink4Admins"}]}

    def start_web_authn_registration(self, **kwargs):
        assert kwargs == {"AccessToken": "access-token"}
        return {
            "CredentialCreationOptions": {
                "challenge": "AQID",
                "rp": {"id": "dmr.vk2ale.com", "name": "HBlink4"},
                "user": {"id": "BAUG", "name": "tony@example.com", "displayName": "Tony"},
                "pubKeyCredParams": [{"alg": -7, "type": "public-key"}],
            }
        }

    def complete_web_authn_registration(self, **kwargs):
        self.completed_registration = kwargs
        return {}

    def list_web_authn_credentials(self, **kwargs):
        assert kwargs == {"AccessToken": "access-token"}
        return {
            "Credentials": [
                {
                    "CredentialId": "cred-1",
                    "FriendlyCredentialName": "YubiKey 5",
                    "AuthenticatorAttachment": "cross-platform",
                    "RelyingPartyId": "dmr.vk2ale.com",
                }
            ]
        }

    def delete_web_authn_credential(self, **kwargs):
        self.deleted = kwargs
        return {}


def config():
    return {
        "region": "ap-southeast-2",
        "user_pool_id": "ap-southeast-2_example",
        "client_id": "client123",
        "admin_group": "HBlink4Admins",
    }


def test_webauthn_login_round_trip_keeps_cognito_session_server_side():
    client = FakeClient()
    bridge = CognitoWebAuthnBridge(CognitoAdminAuthenticator(config(), client=client))

    start = bridge.start_login("tony@example.com")
    assert start.challenge_token
    assert start.challenge_token != "webauthn-session"
    assert start.public_key["rpId"] == "dmr.vk2ale.com"

    credential = {
        "id": "credential-id",
        "rawId": "AQID",
        "type": "public-key",
        "response": {
            "clientDataJSON": "AQID",
            "authenticatorData": "BAUG",
            "signature": "BwgJ",
            "userHandle": None,
        },
    }
    completed = bridge.complete_login(start.challenge_token, credential)
    assert completed.identity.username == "tony@example.com"
    assert completed.access_token == "access-token"
    assert client.challenge_response["ChallengeName"] == "WEB_AUTHN"
    assert client.challenge_response["Session"] == "webauthn-session"
    sent = json.loads(client.challenge_response["ChallengeResponses"]["CREDENTIAL"])
    assert sent == credential


def test_webauthn_registration_list_and_delete():
    client = FakeClient()
    bridge = CognitoWebAuthnBridge(CognitoAdminAuthenticator(config(), client=client))

    options = bridge.start_registration("access-token")
    assert options["rp"]["id"] == "dmr.vk2ale.com"

    credential = {
        "id": "credential-id",
        "rawId": "AQID",
        "type": "public-key",
        "response": {"clientDataJSON": "AQID", "attestationObject": "BAUG"},
    }
    bridge.complete_registration("access-token", credential)
    assert client.completed_registration == {
        "AccessToken": "access-token",
        "Credential": credential,
    }

    listed = bridge.list_credentials("access-token")
    assert listed == [
        {
            "credential_id": "cred-1",
            "name": "YubiKey 5",
            "attachment": "cross-platform",
            "relying_party_id": "dmr.vk2ale.com",
        }
    ]

    bridge.delete_credential("access-token", "cred-1")
    assert client.deleted == {"AccessToken": "access-token", "CredentialId": "cred-1"}
