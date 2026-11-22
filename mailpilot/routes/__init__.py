from .dashboard import dashboard_blueprint
from .health import health_blueprint
from .composer import composer_blueprint
from .campaigns import campaigns_blueprint
from .recipients import recipients_blueprint
from .uploads import uploads_blueprint
from .analytics import analytics_blueprint
from .settings import settings_blueprint
from .notifications import notifications_blueprint
from .security import security_blueprint
from .auth import auth_blueprint
from .unsubscribe import unsubscribe_blueprint


def register_blueprints(app):
    app.register_blueprint(unsubscribe_blueprint)
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(dashboard_blueprint)
    app.register_blueprint(composer_blueprint)
    app.register_blueprint(campaigns_blueprint)
    app.register_blueprint(health_blueprint)
    app.register_blueprint(recipients_blueprint)
    app.register_blueprint(uploads_blueprint)
    app.register_blueprint(analytics_blueprint)
    app.register_blueprint(settings_blueprint)
    app.register_blueprint(notifications_blueprint)
    app.register_blueprint(security_blueprint)
