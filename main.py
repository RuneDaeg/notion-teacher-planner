"""Firebase deployment entry point. Importing this does not start any schedules."""
from firebase_functions import https_fn, scheduler_fn, tasks_fn, options, params
from firebase_admin import initialize_app, get_app, firestore

from cloud.app import create_app
from cloud.store import Store
from cloud.runtime import REGION, enqueue, dispatch_daily, run_task

NEIS_KEY = params.SecretParam('NEIS_API_KEY')
NOTION_SECRET = params.SecretParam('NOTION_CLIENT_SECRET')
CIPHER_KEY = params.SecretParam('TOKEN_ENCRYPTION_KEY')
CLIENT_ID = params.StringParam('NOTION_CLIENT_ID')
PUBLIC_URL = params.StringParam('PUBLIC_BASE_URL')


def store():
    try:
        get_app()
    except ValueError:
        initialize_app()
    return Store(firestore.client())


@https_fn.on_request(region=REGION, max_instances=2, timeout_sec=120,
                     secrets=[NOTION_SECRET, CIPHER_KEY])
def planner_api(req: https_fn.Request) -> https_fn.Response:
    app = create_app(store=store(), enqueue=enqueue, public_url=PUBLIC_URL.value,
                     client_id=CLIENT_ID.value, client_secret=NOTION_SECRET.value,
                     encryption_key=CIPHER_KEY.value)
    with app.request_context(req.environ):
        return app.full_dispatch_request()


@scheduler_fn.on_schedule(schedule='0 7 * * *', timezone='Asia/Seoul', region=REGION,
                          max_instances=1, timeout_sec=540, retry_count=3)
def planner_daily(_event: scheduler_fn.ScheduledEvent) -> None:
    dispatch_daily(store())


@tasks_fn.on_task_dispatched(region=REGION, max_instances=1, concurrency=1, timeout_sec=540,
    invoker='private', secrets=[NEIS_KEY, NOTION_SECRET, CIPHER_KEY],
    retry_config=options.RetryConfig(max_attempts=8, min_backoff_seconds=60,
                                     max_backoff_seconds=900),
    rate_limits=options.RateLimits(max_concurrent_dispatches=1, max_dispatches_per_second=1))
def planner_sync_task(req: tasks_fn.CallableRequest) -> dict:
    return run_task(store(), req.data, api_key=NEIS_KEY.value, encryption_key=CIPHER_KEY.value,
                    client_id=CLIENT_ID.value, client_secret=NOTION_SECRET.value,
                    public_url=PUBLIC_URL.value)
