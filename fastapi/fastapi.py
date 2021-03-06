import os
import time
import logging
from logging.handlers import TimedRotatingFileHandler

from celery import Celery
from werkzeug.utils import find_modules, import_string
from raven.contrib.flask import Sentry
from sqlalchemy import engine, event
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cache import Cache

from fastapi.utils.error import APIError
from fastapi.utils.error_handlers import error_404_handler, error_429_handler, error_handler
from fastapi.utils.hooks import before_request_handler, after_request_handler
from fastapi.utils.redis_client import init_redis_client
from fastapi.config import config


app = Flask(__name__)
db = SQLAlchemy()
ms = Marshmallow(app)
celery = Celery(app.import_name)
limiter = Limiter(key_func=get_remote_address)
cache = Cache()


__all__ = [app, db]


def init_config():
    run_mode = os.environ.get('FASTAPI_MODE', 'dev')
    app.config.from_object(config[run_mode])


def init_logger():
    LEVELS = {'debug': logging.DEBUG,
              'info':  logging.INFO,
              'warning': logging.WARNING,
              'error': logging.ERROR,
              'critical': logging.CRITICAL}

    log_dir = os.path.join(app.config['LOG_PATH'])
    log_file = os.path.join(app.config['LOG_PATH'], app.config['LOG_FILENAME'])
    if not os.path.isdir(log_dir):
        os.mkdir(log_dir)

    log_level = LEVELS.get(app.config['LOG_LEVEL'].lower(), 'info')

    rotate_handler = TimedRotatingFileHandler(log_file, "D", 1, 30)
    rotate_handler.suffix = "%Y%m%d.log"
    rotate_handler.setLevel(log_level)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)

    formatter = logging.Formatter('%(asctime)-10s %(levelname)s %(filename)s %(lineno)d %(process)d %(message)s')
    rotate_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)

    app.logger.addHandler(stream_handler)
    app.logger.addHandler(rotate_handler)

    app.logger.info('初始化日志成功')


@event.listens_for(engine.Engine, 'before_cursor_execute')
def before_cursor_execute(conn, cursor, statement, parameters,
                          context, exceutemany):
    context.query_start_ts = int(time.time() * 1000)


@event.listens_for(engine.Engine, 'after_cursor_execute')
def after_cursor_execute(conn, cursor, statement, parameters,
                         context, exceutemany):
    cost = int(time.time() * 1000) - context.query_start_ts
    str_params = ''
    if isinstance(parameters, dict):
        for name, val in parameters.items():
            str_params += '{}:{}, '.format(name, val)
    elif isinstance(parameters, tuple):
        str_params = ', '.join([str(item) for item in parameters])

    str_statement = statement.replace('\n', ' ')
    sql_log = '[cost: {}ms]sql: {}, params: {}'.format(cost, str_statement, str_params)
    app.logger.debug(sql_log)


def init_db():
    db.init_app(app)
    app.logger.info('初始化db成功')

def init_redis():
    init_redis_client(app)
    app.logger.info('初始化Redis成功')


def register_blueprints():
    modules = find_modules('fastapi.api', recursive=True)
    for name in modules:
        module = import_string(name)
        if hasattr(module, 'bp'):
            app.register_blueprint(module.bp)


def init_error_handlers():
    app.register_error_handler(404, error_404_handler)
    app.register_error_handler(429, error_429_handler)
    app.register_error_handler(APIError, error_handler)


def init_hooks():
    app.before_request(before_request_handler)
    app.after_request(after_request_handler)


def init_sentry():
    enable_sentry = app.config.get('ENABLE_SENTRY', False)
    if not enable_sentry:
        pass

    Sentry(app, dsn=app.config.get('SENTRY_DSN'))
    app.logger.info('初始化Sentry成功')


def init_celery():
    celery.conf.update(app.config)
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask
    app.logger.info('初始化celery成功')



def init_limiter():
    limiter.init_app(app)


def init_cache():
    cache.init_app(app)


def register_signal_handlers():
    from fastapi.signals import register_signal_handlers as _register_signal_handlers
    _register_signal_handlers()


def init_app():
    init_config()
    init_logger()
    init_error_handlers()
    init_hooks()
    init_db()
    init_redis()
    init_sentry()
    init_celery()
    init_limiter()
    init_cache()
    register_blueprints()
    register_signal_handlers()


init_app()