from flask import Flask
#from flask_toastr import Toastr
import os
#from flask_session import Session
import logging

app = Flask(__name__)
app.config.update(SECRET_KEY=os.urandom(24))
app.logger.setLevel(logging.INFO)

# init toaster
#toastr = Toastr(app)

# Check Configuration section for more details
#app.config['SESSION_TYPE'] = 'filesystem'
#Session(app)

from app import routes
