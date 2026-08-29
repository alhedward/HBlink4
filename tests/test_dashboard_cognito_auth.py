import pytest

from dashboard.cognito_auth import (
    CognitoAdminAuthenticator,
    CognitoAuthorizationError,
    CognitoChallengeError,
    CognitoInvalidCredentials,
)


class FakeCognitoError(Exception):
    def __init__(self, code, message="error"):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class FakeClient:
    def __init__(self):
        self.mode = "success"
        self.last_auth = None
        self.last_challenge = None
        self.forgot_calls = []
        self.confirm_calls = []

    def admin_initiate_auth(self, **kwargs):
        self.last_auth = kwargs
        if self.mode == "invalid":
            raise FakeCognitoError("NotAuthorizedException", "bad credentials")
        if self.mode == "new_password":
            return {
                "ChallengeName": "NEW_PASSWORD_REQUIRED",
                "Session": "cognito-session",
                "ChallengeParameters": {"requiredAttributes": '["userAttributes.email"]'},
            }
        return {"AuthenticationResult": {"AccessToken": "access-token"}}

    def get_user(self, **kwargs):
        assert kwargs == {"AccessToken": "access-token"}
        return {"Username": "tony"}

    def admin_list_groups_for_user(self, **kwargs):
        if self.mode == "wrong_group":
            return {"Groups": [{"GroupName": "Other"}]}
        return {"Groups": [{"GroupName": "HBlink4Admins"}, {"GroupName": "Operators"}]}

    def admin_respond_to_auth_challenge(self, **kwargs):
        self.last_challenge = kwargs
        return {"AuthenticationResult": {"AccessToken": "access-token"}}

    def forgot_password(self, **kwargs):
        self.forgot_calls.append(kwargs)
        return {"CodeDeliveryDetails": {"Destination": "t***@example.com", "DeliveryMedium": "EMAIL"}}

    def confirm_forgot_password(self, **kwargs):
        self.confirm_calls.append(kwargs)
        return {}


def config(**overrides):
    value = {
        "region": "ap-southeast-2",
        "user_pool_id": "ap-southeast-2_example",
        "client_id": "client123",
        "admin_group": "HBlink4Admins",
    }
    value.update(overrides)
    return value


def test_successful_login_requires_admin_group():
    fake = FakeClient()
    auth = CognitoAdminAuthenticator(config(), client=fake)
    result = auth.authenticate("tony", "secret")
    assert result.status == "authenticated"
    assert result.identity.username == "tony"
    assert "HBlink4Admins" in result.identity.groups
    assert fake.last_auth["AuthFlow"] == "ADMIN_USER_PASSWORD_AUTH"


def test_authenticated_non_admin_is_rejected():
    fake = FakeClient()
    fake.mode = "wrong_group"
    auth = CognitoAdminAuthenticator(config(), client=fake)
    with pytest.raises(CognitoAuthorizationError):
        auth.authenticate("tony", "secret")


def test_invalid_credentials_are_normalized():
    fake = FakeClient()
    fake.mode = "invalid"
    auth = CognitoAdminAuthenticator(config(), client=fake)
    with pytest.raises(CognitoInvalidCredentials):
        auth.authenticate("tony", "bad")


def test_invited_user_new_password_challenge_stays_server_side():
    fake = FakeClient()
    fake.mode = "new_password"
    auth = CognitoAdminAuthenticator(config(), client=fake)
    result = auth.authenticate("tony", "temporary")
    assert result.status == "new_password_required"
    assert result.challenge_token
    assert result.challenge_token != "cognito-session"
    assert result.required_attributes == ("userAttributes.email",)

    fake.mode = "success"
    identity = auth.complete_new_password(result.challenge_token, "new-secret")
    assert identity.username == "tony"
    assert fake.last_challenge["Session"] == "cognito-session"
    assert fake.last_challenge["ChallengeResponses"]["NEW_PASSWORD"] == "new-secret"

    with pytest.raises(CognitoChallengeError):
        auth.complete_new_password(result.challenge_token, "another-secret")


def test_optional_client_secret_hash_is_sent_to_cognito():
    fake = FakeClient()
    auth = CognitoAdminAuthenticator(config(client_secret="client-secret"), client=fake)
    auth.authenticate("tony", "secret")
    assert fake.last_auth["AuthParameters"]["SECRET_HASH"]


def test_password_reset_round_trip():
    fake = FakeClient()
    auth = CognitoAdminAuthenticator(config(client_secret="client-secret"), client=fake)
    details = auth.start_password_reset("tony")
    assert details["DeliveryMedium"] == "EMAIL"
    assert fake.forgot_calls[-1]["SecretHash"]

    auth.confirm_password_reset("tony", "123456", "new-secret")
    assert fake.confirm_calls[-1]["ConfirmationCode"] == "123456"
    assert fake.confirm_calls[-1]["SecretHash"]


def test_admin_management_invite_list_and_reset():
    fake = FakeClient()
    fake.list_users_in_group = lambda **kwargs: {
        "Users": [
            {
                "Username": "tony",
                "Enabled": True,
                "UserStatus": "CONFIRMED",
                "Attributes": [{"Name": "email", "Value": "tony@example.com"}],
            }
        ]
    }
    created = []
    grouped = []
    resets = []
    resent = []

    def create_user(**kwargs):
        if kwargs.get("MessageAction") == "RESEND":
            resent.append(kwargs)
        else:
            created.append(kwargs)
        return {"User": {"Username": kwargs["Username"]}}

    fake.admin_create_user = create_user
    fake.admin_add_user_to_group = lambda **kwargs: grouped.append(kwargs) or {}
    fake.admin_reset_user_password = lambda **kwargs: resets.append(kwargs) or {}

    auth = CognitoAdminAuthenticator(config(), client=fake)
    users = auth.list_admin_users()
    assert users == [
        {
            "username": "tony",
            "email": "tony@example.com",
            "enabled": True,
            "status": "CONFIRMED",
        }
    ]

    username = auth.invite_admin("NEW@example.com")
    assert username == "new@example.com"
    assert created[-1]["Username"] == "new@example.com"
    assert grouped[-1]["GroupName"] == "HBlink4Admins"

    auth.resend_invite("new@example.com")
    assert resent[-1]["MessageAction"] == "RESEND"

    auth.reset_admin_password("tony")
    assert resets[-1]["Username"] == "tony"
