from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
import os

# Load .env variables
load_dotenv()

def create_app():
    app = Flask(__name__)

    # Enable CORS
    CORS(app, origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "https://nepwoop.com"
    ], supports_credentials=True)

    from app.routes.bot_routes import bot_bp
    app.register_blueprint(bot_bp, url_prefix="/api")

    @app.route("/")
    def health_check():
        return {"message": "Backend is running!"}

    return app

if __name__ == "__main__":
    app = create_app()
    # Run on port 8000
    app.run(debug=True, port=8000)
