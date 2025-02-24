import os
import secrets
from datetime import timedelta
from pathlib import Path
from flask import Flask, send_from_directory
from flask_jwt_extended import JWTManager


from .utils import error
from .config import config

# Import api route blueprint before importing routes and register as blueprint
from .routes import api, auth


build_dir = os.path.join(Path(__file__).parent, "..", "ui", "build")

print(build_dir)

# Create Flask Application
app = Flask(__name__, static_folder=build_dir)

# Configure Flask app
db_prefix = os.getenv('DB_PREFIX', 'db_')
app.config.update(config.configuration)


# Configure application to store JWTs in cookies. Whenever you make
# a request to a protected endpoint, you will need to send in the
# access or refresh JWT via a cookie or an Authorization Bearer header.
app.config['JWT_TOKEN_LOCATION'] = ['headers']
app.config['JWT_COOKIE_SECURE'] = True
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(minutes=30)
app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(minutes=60)
app.config['JWT_COOKIE_CSRF_PROTECT'] = True
app.config['JWT_ENCODE_ISSUER'] = app.config['ID']
app.config['JWT_DECODE_ISSUER'] = app.config['ID']

# Secret key is used to sign JWT tokens generated by our application
# (from flask documentation)
_default_secret_key = secrets.token_hex()
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', _default_secret_key)

assert app.config["JWT_ACCESS_TOKEN_EXPIRES"] < app.config["JWT_REFRESH_TOKEN_EXPIRES"], \
    "Error: Wrong JWT expiration times. Please make sure that JWT_ACCESS_TOKEN_EXPIRES < JWT_REFRESH_TOKEN_EXPIRES"

jwt = JWTManager(app)
app.register_blueprint(api)
app.register_blueprint(auth)

# Setup basepath for frontend
base_path = os.environ.get("REACT_APP_BASE_PATH", '')
root_path = base_path or '/'
# Routes for react build directory
@app.route(f'{root_path}', defaults={'path': ''}, methods=['GET'])
@app.route(f'{base_path}/<path:path>')
def index(path):
    """ The index route. This route should render the
        React build files. Which is located at the front-end folder.
    """

    if path != "" and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')


@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    # custom error message (I guess it will be sent no matter if refresh or access tokens are expired)
    err = error(msg="Session has expired! Please login again")
    return err, 401


def hsts_header():
    """Returns the proper HSTS policy."""
    hsts_age = 100_000
    hsts_policy = 'max-age={0}'.format(hsts_age)
    hsts_policy += '; includeSubDomains'

    return hsts_policy


# Run the application
if __name__ == '__main__':
    # Start Flask
    app.run(debug=app.config['DEBUG'])
