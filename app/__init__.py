"""Flask application factory."""

from dotenv import load_dotenv
from flask import Flask, redirect, url_for

from app.config import Config


def create_app():
    """Build and configure the Flask app: load .env, apply Config, register
    the documents and chat blueprints, and add a "/" redirect so opening the
    app takes you straight to the upload page. Using a factory (instead of a
    module-level app object) keeps wsgi.py and any future tests able to
    create independent app instances."""
    load_dotenv()

    app = Flask(__name__)
    app.config.from_object(Config)

    from app.documents.routes import documents_bp
    from app.chat.routes import chat_bp

    app.register_blueprint(documents_bp, url_prefix="/documents")
    app.register_blueprint(chat_bp, url_prefix="/chat")

    @app.route("/")
    def index():
        """Redirect the bare root URL to the upload page, since uploading a
        document is the natural first step of the demo."""
        return redirect(url_for("documents.upload"))

    return app
