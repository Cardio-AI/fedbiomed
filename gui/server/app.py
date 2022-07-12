import os
from datetime import timedelta

from flask import Flask, render_template, send_from_directory, request, redirect, url_for
from flask_jwt_extended import JWTManager
from http import HTTPStatus

from config import Config
# Import api route blueprint before importing routes
# and register as blueprint
from routes import api

build_dir = os.getenv('BUILD_DIR', '../ui/gui-build')

# Create Flask Application
app = Flask(__name__, static_folder=build_dir)

# Configure Flask app
db_prefix = os.getenv('DB_PREFIX', 'db_')
config = Config()
app.config.update(config.generate_config())

# Configure application to store JWTs in cookies. Whenever you make
# a request to a protected endpoint, you will need to send in the
# access or refresh JWT via a cookie or an Authorization Bearer header.
app.config['JWT_TOKEN_LOCATION'] = ['headers', 'cookies']
app.config['JWT_COOKIE_SECURE'] = True
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
app.config['JWT_COOKIE_CSRF_PROTECT'] = True

# Secret key is used to sign JWT tokens generated by our application
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'CHANGE ME')

# Add certificate and private key paths for HTTPS
CERTIFICATE_NAME = os.getenv('CERTIFICATE_NAME', None)
PRIVATE_KEY_NAME = os.getenv('PRIVATE_KEY_NAME', None)

jwt = JWTManager(app)
app.register_blueprint(api)




# Routes for react build directory
@app.route('/', defaults={'path': ''}, methods=['GET'])
@app.route('/<path:path>')
def index(path):
    """ The index route. This route should render the
        React build files. Which is located at the front-end folder.
    """
    before_request(request)
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

    return render_template('index.html')


<<<<<<< HEAD
# functions for HTTP to HTTPS redirections
@app.before_request
def before_request(req=None):
    if request.scheme == 'http':
            return redirect(url_for(request.endpoint,
                                    _scheme='https',
                                    _external=True),
                            HTTPStatus.PERMANENT_REDIRECT)
# def before_request(req=None):
#     print(request)
#     # redirect connections from HTTP to HTTPS
#     print("REDIRECTION STUFF")
#     if request.url.startswith('http://'):
#         print("REDIRECTION")
#         url = request.url.replace('http://', 'https://', 1).replace('080', '443', 1)
#         return redirect(url, code=302)

@app.after_request
def set_hsts_header(response):
        """Adds HSTS header to each response."""
        # Should we add STS header?
        if request.is_secure:
            response.headers.setdefault(
                'Strict-Transport-Security', hsts_header)
        return response

def hsts_header():
    """Returns the proper HSTS policy."""
    hsts_age = 100000
    hsts_policy = 'max-age={0}'.format(hsts_age)

    if self.hsts_include_subdomains:
        hsts_policy += '; includeSubDomains'

    return hsts_policy


#def context()
=======
>>>>>>> 98a8f6a0e14c38743fe78be90c4589fe0a479e2d
# Run the application
if __name__ == '__main__':

    # Start Flask
    if CERTIFICATE_NAME and PRIVATE_KEY_NAME:
        context = ('../../etc/' + CERTIFICATE_NAME, '../../etc/' + PRIVATE_KEY_NAME)
    else:
        context = "adhoc"
    # Process(target=before_request,
    #         daemon=True).start()
    app.run(host=app.config['HOST'],
            port=app.config['PORT'],
            debug=app.config['DEBUG'],
            ssl_context=context)

# TODO : Implement method to retrieve user password
# TODO : Add refresh token as cookie and change remember me token validity
# TODO : Update Readme for SSL option
# TODO : Callback refresh token at Flask server level vs refresh token endpoint call from frontend
