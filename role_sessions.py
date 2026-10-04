"""Keep organizer authentication independent of participant browser tabs."""
from flask import request
from flask.sessions import SecureCookieSessionInterface


class RoleSessionInterface(SecureCookieSessionInterface):
    @staticmethod
    def is_organizer_request():
        path = request.path
        return path == "/admin" or path.startswith(("/admin/", "/api/admin/"))

    def get_cookie_name(self, app):
        if self.is_organizer_request():
            return app.config["ADMIN_SESSION_COOKIE_NAME"]
        return super().get_cookie_name(app)

    def get_signing_serializer(self, app):
        serializer = super().get_signing_serializer(app)
        if serializer is not None and self.is_organizer_request():
            # A participant cookie cannot be reused as an organizer cookie.
            # The serializer is local to this request, unlike the interface.
            serializer.salt = f"{self.salt}:organizer"
        return serializer
