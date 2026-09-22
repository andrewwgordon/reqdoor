from flask import Flask
from .extensions import appbuilder, db
from flask_appbuilder import Model

def create_app(config_overrides=None) -> Flask:
    app = Flask(__name__)
    app.config.from_object('config')
    if config_overrides:
        app.config.update(config_overrides)
    with app.app_context():
        db.init_app(app)
        from .views import register_views
        appbuilder.init_app(app, db.session)
        register_views(appbuilder)
        Model.metadata.create_all(db.engine)

    return app