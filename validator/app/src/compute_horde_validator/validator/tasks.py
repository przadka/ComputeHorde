import asyncio
import time
from datetime import timedelta

import billiard.exceptions
import bittensor
import celery.exceptions
import torch
from bittensor.extrinsics.set_weights import set_weights_extrinsic
from bittensor.utils.weight_utils import process_weights_for_netuid
from celery.result import allow_join_result
from celery.utils.log import get_task_logger
from django.conf import settings
from django.utils.timezone import now

from compute_horde_validator.celery import app
from compute_horde_validator.validator.models import SyntheticJobBatch
from compute_horde_validator.validator.synthetic_jobs.utils import execute_jobs, initiate_jobs

logger = get_task_logger(__name__)

JOB_WINDOW = 60 * 60
SYNTHETIC_JOBS_SOFT_LIMIT = 300
SYNTHETIC_JOBS_HARD_LIMIT = 305

SCORING_ALGO_VERSION = 1

WEIGHT_SETTING_TTL = 60
WEIGHT_SETTING_HARD_TTL = 65
WEIGHT_SETTING_ATTEMPTS = 100
MEANINGFUL_VALIDATOR_STAKE_THRESHOLD_TAO = 9


@app.task(
    soft_time_limit=SYNTHETIC_JOBS_SOFT_LIMIT,
    time_limit=SYNTHETIC_JOBS_HARD_LIMIT,
)
def _run_synthetic_jobs():
    jobs = initiate_jobs(settings.BITTENSOR_NETUID, settings.BITTENSOR_NETWORK)  # metagraph will be refetched and
    # that's fine, after sleeping for e.g. 30 minutes we should refetch the miner list
    if not jobs:
        logger.info('Nothing to do')
        return
    try:
        asyncio.run(execute_jobs(jobs))
    except billiard.exceptions.SoftTimeLimitExceeded:
        logger.info('Running synthetic jobs timed out')


@app.task()
def run_synthetic_jobs():
    if not settings.DEBUG_DONT_STAGGER_VALIDATORS:
        metagraph = bittensor.metagraph(settings.BITTENSOR_NETUID, settings.BITTENSOR_NETWORK)
        my_key = settings.BITTENSOR_WALLET().get_hotkey().ss58_address
        validator_keys = sorted([n.hotkey for n in metagraph.neurons if
                                 n.validator_permit and n.stake.tao >= MEANINGFUL_VALIDATOR_STAKE_THRESHOLD_TAO])
        if my_key not in validator_keys:
            raise ValueError(f"Can't determine proper synthetic job window due to stake being < "
                             f"{MEANINGFUL_VALIDATOR_STAKE_THRESHOLD_TAO}")
        my_index = validator_keys.index(my_key)
        window_per_validator = JOB_WINDOW / (len(validator_keys) + 1)
        my_window_starts_at = window_per_validator * my_index
        logger.info(f'Sleeping for {my_window_starts_at:02f}s because I am {my_index} out of {len(validator_keys)}')
        time.sleep(my_window_starts_at)
    _run_synthetic_jobs.apply_async()


@app.task()
def do_set_weights(
    subtensor_chain_endpoint: str,
    netuid: int,
    uids: list,
    weights: list,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    version_key: int,
):
    """
    Set weights. To be used in other celery tasks in order to facilitate a timeout,
     since the multiprocessing version of this doesn't work in celery.
    """
    bittensor.turn_console_off()
    return set_weights_extrinsic(
        subtensor_endpoint=subtensor_chain_endpoint,
        wallet=settings.BITTENSOR_WALLET(),
        netuid=netuid,
        uids=torch.LongTensor(uids),
        weights=torch.FloatTensor(weights),
        version_key=version_key,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )


@app.task
def set_scores():
    subtensor = bittensor.subtensor(network=settings.BITTENSOR_NETWORK)
    metagraph = subtensor.metagraph(netuid=settings.BITTENSOR_NETUID)
    neurons = metagraph.neurons
    hotkey_to_uid = {n.hotkey: n.uid for n in neurons}
    score_per_uid = {}
    batches = list(SyntheticJobBatch.objects.prefetch_related('synthetic_jobs').filter(
        scored=False, started_at__gte=now() - timedelta(days=1), accepting_results_until__lt=now()))
    if not batches:
        logger.info('No batches - nothing to score')
        return

    for batch in batches:
        for job in batch.synthetic_jobs.all():
            uid = hotkey_to_uid.get(job.miner.hotkey)
            if not uid:
                continue
            score_per_uid[uid] = score_per_uid.get(uid, 0) + job.score
    if not score_per_uid:
        logger.info('No miners on the subnet to score')
        return
    uids = torch.zeros(len(neurons), dtype=torch.long)
    weights = torch.zeros(len(neurons), dtype=torch.float32)
    for ind, n in enumerate(neurons):
        uids[ind] = n.uid
        weights[ind] = score_per_uid.get(n.uid, 0)

    uids, weights = process_weights_for_netuid(
        uids,
        weights,
        settings.BITTENSOR_NETUID,
        subtensor,
        metagraph,
    )
    for try_number in range(WEIGHT_SETTING_ATTEMPTS):
        logger.debug(f'Setting weights (attempt #{try_number}):\nuids={uids}\nscores={weights}')
        success = False
        try:

            result = do_set_weights.apply_async(
                kwargs=dict(
                    subtensor_chain_endpoint=subtensor.chain_endpoint,
                    netuid=settings.BITTENSOR_NETUID,
                    uids=uids.tolist(),
                    weights=weights.tolist(),
                    wait_for_inclusion=True,
                    wait_for_finalization=False,
                    version_key=SCORING_ALGO_VERSION,
                ),
                soft_time_limit=WEIGHT_SETTING_TTL,
                time_limit=WEIGHT_SETTING_HARD_TTL,
            )
            logger.info(f'Setting weights task id: {result.id}')
            try:
                with allow_join_result():
                    success = result.get(timeout=WEIGHT_SETTING_TTL)
            except (celery.exceptions.TimeoutError, billiard.exceptions.TimeLimitExceeded):
                result.revoke(terminate=True)
                logger.info(f'Setting weights timed out (attempt #{try_number})')
        except Exception:
            logger.exception('Encountered when setting weights: ')
        if not success:
            logger.info(f'Failed to set weights (attempt #{try_number})')
        else:
            logger.info(f'Successfully set weights!!! (attempt #{try_number})')
            break
    else:
        raise RuntimeError(f'Failed to set weights after {WEIGHT_SETTING_ATTEMPTS} attempts')
