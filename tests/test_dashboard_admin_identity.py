from dashboard.admin_identity import CognitoAdminIdentityService
from dashboard.cognito_auth import CognitoAdminAuthenticator


class FakeCognito:
    def __init__(self):
        self.created = None
        self.grouped = None
        self.updated = None

    def get_user(self, **kwargs):
        return {
            "Username": "tony@example.com",
            "UserAttributes": [
                {"Name": "email", "Value": "tony@example.com"},
                {"Name": "given_name", "Value": "Tony"},
                {"Name": "family_name", "Value": "Edward"},
                {"Name": "nickname", "Value": "VK2ALE"},
            ],
        }

    def update_user_attributes(self, **kwargs):
        self.updated = kwargs
        return {}

    def admin_create_user(self, **kwargs):
        self.created = kwargs
        return {"User": {"Username": kwargs["Username"]}}

    def admin_add_user_to_group(self, **kwargs):
        self.grouped = kwargs
        return {}

    def list_users_in_group(self, **kwargs):
        return {"Users": []}


class FakeSes:
    def __init__(self):
        self.sent = None

    def send_email(self, **kwargs):
        self.sent = kwargs
        return {"MessageId": "message-1"}


def config():
    return {
        "region": "ap-southeast-2",
        "user_pool_id": "ap-southeast-2_example",
        "client_id": "client123",
        "admin_group": "HBlink4Admins",
    }


def test_profile_cleanup_uses_standard_cognito_attributes():
    client = FakeCognito()
    service = CognitoAdminIdentityService(
        CognitoAdminAuthenticator(config(), client=client), ses_client=FakeSes()
    )

    profile = service.get_profile("token")
    assert profile["given_name"] == "Tony"
    assert profile["family_name"] == "Edward"
    assert profile["callsign"] == "VK2ALE"
    assert service.profile_complete(profile) is True

    service.update_profile(
        "token",
        {"given_name": "Tony", "family_name": "Edward", "callsign": "vk2ale"},
    )
    assert client.updated["UserAttributes"] == [
        {"Name": "given_name", "Value": "Tony"},
        {"Name": "family_name", "Value": "Edward"},
        {"Name": "nickname", "Value": "VK2ALE"},
    ]


def test_personalised_invite_is_html_and_suppresses_cognito_default(monkeypatch):
    monkeypatch.setenv("HBLINK4_ADMIN_INVITE_FROM", "webmaster@sgars.vk2ale.com")
    monkeypatch.setenv("HBLINK4_ADMIN_URL", "https://dmr.vk2ale.com/admin")
    client = FakeCognito()
    ses = FakeSes()
    service = CognitoAdminIdentityService(
        CognitoAdminAuthenticator(config(), client=client), ses_client=ses
    )
    inviter = {
        "username": "tony@example.com",
        "email": "tony@example.com",
        "given_name": "Tony",
        "family_name": "Edward",
        "callsign": "VK2ALE",
    }

    username = service.invite_admin(
        {
            "email": "jane@example.com",
            "given_name": "Jane",
            "family_name": "Smith",
            "callsign": "VK2XYZ",
        },
        inviter,
    )

    assert username == "jane@example.com"
    assert client.created["MessageAction"] == "SUPPRESS"
    attrs = {item["Name"]: item["Value"] for item in client.created["UserAttributes"]}
    assert attrs["given_name"] == "Jane"
    assert attrs["family_name"] == "Smith"
    assert attrs["nickname"] == "VK2XYZ"
    assert client.grouped["GroupName"] == "HBlink4Admins"

    assert ses.sent["FromEmailAddress"] == "webmaster@sgars.vk2ale.com"
    assert ses.sent["ReplyToAddresses"] == ["tony@example.com"]
    html_body = ses.sent["Content"]["Simple"]["Body"]["Html"]["Data"]
    assert "Hi Jane" in html_body
    assert "VK2XYZ" in html_body
    assert "https://dmr.vk2ale.com/admin" in html_body
    assert "at least 12 characters" in html_body
    assert "Tony Edward (VK2ALE)" in html_body
