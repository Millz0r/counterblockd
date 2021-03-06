import os
import logging
import pymongo

from lib import config, cache, util
from lib.processor import RollbackProcessor

logger = logging.getLogger(__name__)

def get_connection():
    """Connect to mongodb, returning a connection object"""
    logger.info("Connecting to mongoDB backend ...")
    mongo_client = pymongo.MongoClient(config.MONGODB_CONNECT, config.MONGODB_PORT)
    mongo_db = mongo_client[config.MONGODB_DATABASE] #will create if it doesn't exist
    if config.MONGODB_USER and config.MONGODB_PASSWORD:
        if not mongo_db.authenticate(config.MONGODB_USER, config.MONGODB_PASSWORD):
            raise Exception("Could not authenticate to mongodb with the supplied username and password.")
    return mongo_db

def init_base_indexes(mongo_db):
    """insert mongo indexes if need-be (i.e. for newly created database)"""
    ##COLLECTIONS THAT ARE PURGED AS A RESULT OF A REPARSE
    #processed_blocks
    mongo_db.processed_blocks.ensure_index('block_index', unique=True)
    #tracked_assets
    mongo_db.tracked_assets.ensure_index('asset', unique=True)
    mongo_db.tracked_assets.ensure_index('_at_block') #for tracked asset pruning
    mongo_db.tracked_assets.ensure_index([
        ("owner", pymongo.ASCENDING),
        ("asset", pymongo.ASCENDING),
    ])
    #trades
    mongo_db.trades.ensure_index([
        ("base_asset", pymongo.ASCENDING),
        ("quote_asset", pymongo.ASCENDING),
        ("block_time", pymongo.DESCENDING)
    ])
    mongo_db.trades.ensure_index([ #tasks.py and elsewhere (for singlular block_index index access)
        ("block_index", pymongo.ASCENDING),
        ("base_asset", pymongo.ASCENDING),
        ("quote_asset", pymongo.ASCENDING)
    ])

    #balance_changes
    mongo_db.balance_changes.ensure_index('block_index')
    mongo_db.balance_changes.ensure_index([
        ("address", pymongo.ASCENDING),
        ("asset", pymongo.ASCENDING),
        ("block_time", pymongo.ASCENDING)
    ])
    #asset_market_info
    mongo_db.asset_market_info.ensure_index('asset', unique=True)
    #asset_marketcap_history
    mongo_db.asset_marketcap_history.ensure_index('block_index')
    mongo_db.asset_marketcap_history.ensure_index([ #tasks.py
        ("market_cap_as", pymongo.ASCENDING),
        ("asset", pymongo.ASCENDING),
        ("block_index", pymongo.DESCENDING)
    ])
    mongo_db.asset_marketcap_history.ensure_index([ #api.py
        ("market_cap_as", pymongo.ASCENDING),
        ("block_time", pymongo.DESCENDING)
    ])
    #asset_pair_market_info
    mongo_db.asset_pair_market_info.ensure_index([ #event.py, api.py
        ("base_asset", pymongo.ASCENDING),
        ("quote_asset", pymongo.ASCENDING)
    ], unique=True)
    mongo_db.asset_pair_market_info.ensure_index('last_updated')
    #asset_extended_info
    mongo_db.asset_extended_info.ensure_index('asset', unique=True)
    mongo_db.asset_extended_info.ensure_index('info_status')
    #btc_open_orders
    mongo_db.btc_open_orders.ensure_index('when_created')
    mongo_db.btc_open_orders.ensure_index('order_tx_hash', unique=True)
    #transaction_stats
    mongo_db.transaction_stats.ensure_index([ #blockfeed.py, api.py
        ("when", pymongo.ASCENDING),
        ("category", pymongo.DESCENDING)
    ])
    mongo_db.transaction_stats.ensure_index('message_index', unique=True)
    mongo_db.transaction_stats.ensure_index('block_index')
    #wallet_stats
    mongo_db.wallet_stats.ensure_index([
        ("when", pymongo.ASCENDING),
        ("network", pymongo.ASCENDING),
    ])
    
    ##COLLECTIONS THAT ARE *NOT* PURGED AS A RESULT OF A REPARSE
    #preferences
    mongo_db.preferences.ensure_index('wallet_id', unique=True)
    mongo_db.preferences.ensure_index('network')
    mongo_db.preferences.ensure_index('last_touched')
    #login_history
    mongo_db.login_history.ensure_index('wallet_id')
    mongo_db.login_history.ensure_index([
        ("when", pymongo.DESCENDING),
        ("network", pymongo.ASCENDING),
        ("action", pymongo.ASCENDING),
    ])
    #chat_handles
    mongo_db.chat_handles.ensure_index('wallet_id', unique=True)
    mongo_db.chat_handles.ensure_index('handle', unique=True)
    #chat_history
    mongo_db.chat_history.ensure_index('when')
    mongo_db.chat_history.ensure_index([
        ("handle", pymongo.ASCENDING),
        ("when", pymongo.DESCENDING),
    ])
    #feeds
    mongo_db.feeds.ensure_index('source')
    mongo_db.feeds.ensure_index('owner')
    mongo_db.feeds.ensure_index('category')
    mongo_db.feeds.ensure_index('info_url')
    #mempool
    mongo_db.mempool.ensure_index('tx_hash')

def get_block_indexes_for_dates(start_dt=None, end_dt=None):
    """Returns a 2 tuple (start_block, end_block) result for the block range that encompasses the given start_date
    and end_date unix timestamps"""
    if start_dt is None:
        start_block_index = config.BLOCK_FIRST
    else:
        start_block = config.mongo_db.processed_blocks.find_one({"block_time": {"$lte": start_dt} }, sort=[("block_time", pymongo.DESCENDING)])
        start_block_index = config.BLOCK_FIRST if not start_block else start_block['block_index']
    
    if end_dt is None:
        end_block_index = config.state['my_latest_block']['block_index']
    else:
        end_block = config.mongo_db.processed_blocks.find_one({"block_time": {"$gte": end_dt} }, sort=[("block_time", pymongo.ASCENDING)])
        if not end_block:
            end_block_index = config.mongo_db.processed_blocks.find_one(sort=[("block_index", pymongo.DESCENDING)])['block_index']
        else:
            end_block_index = end_block['block_index']
    return (start_block_index, end_block_index)

def get_block_time(block_index):
    """TODO: implement result caching to avoid having to go out to the database"""
    block = config.mongo_db.processed_blocks.find_one({"block_index": block_index })
    if not block: return None
    return block['block_time']

def reset_db_state():
    """boom! blow away all applicable collections in mongo"""
    config.mongo_db.processed_blocks.drop()
    config.mongo_db.tracked_assets.drop()
    config.mongo_db.trades.drop()
    config.mongo_db.balance_changes.drop()
    config.mongo_db.asset_market_info.drop()
    config.mongo_db.asset_marketcap_history.drop()
    config.mongo_db.pair_market_info.drop()
    config.mongo_db.btc_open_orders.drop()
    config.mongo_db.asset_extended_info.drop()
    config.mongo_db.transaction_stats.drop()
    config.mongo_db.feeds.drop()
    config.mongo_db.wallet_stats.drop()
    
    #create/update default app_config object
    config.mongo_db.app_config.update({}, {
    'db_version': config.DB_VERSION, #counterblockd database version
    'running_testnet': config.TESTNET,
    'counterpartyd_db_version_major': None,
    'counterpartyd_db_version_minor': None,
    'counterpartyd_running_testnet': None,
    'last_block_assets_compiled': config.BLOCK_FIRST, #for asset data compilation in tasks.py (resets on reparse as well)
    }, upsert=True)
    app_config = config.mongo_db.app_config.find()[0]
    
    #DO NOT DELETE preferences and chat_handles and chat_history
    
    #create XCP and BTC assets in tracked_assets
    for asset in [config.XCP, config.BTC]:
        base_asset = {
            'asset': asset,
            'owner': None,
            'divisible': True,
            'locked': False,
            'total_issued': None,
            '_at_block': config.BLOCK_FIRST, #the block ID this asset is current for
            '_history': [] #to allow for block rollbacks
        }
        config.mongo_db.tracked_assets.insert(base_asset)
        
    #reinitialize some internal counters
    config.state['my_latest_block'] = {'block_index': 0}
    config.state['last_message_index'] = -1
    
    return app_config

def rollback(max_block_index):
    """called if there are any records for blocks higher than this in the database? If so, they were impartially created
       and we should get rid of them
    
    NOTE: after calling this function, you should always trigger a "continue" statement to reiterate the processing loop
    (which will get a new cpd_latest_block from counterpartyd and resume as appropriate)   
    """
    assert isinstance(max_block_index, (int, long)) and max_block_index > 0
    if not config.mongo_db.processed_blocks.find_one({"block_index": max_block_index}):
        raise Exception("Can't roll back to specified block index: %i doesn't exist in database" % max_block_index)
    
    logger.warn("Pruning to block %i ..." % (max_block_index))        
    config.mongo_db.processed_blocks.remove({"block_index": {"$gt": max_block_index}})
    config.mongo_db.balance_changes.remove({"block_index": {"$gt": max_block_index}})
    config.mongo_db.trades.remove({"block_index": {"$gt": max_block_index}})
    config.mongo_db.asset_marketcap_history.remove({"block_index": {"$gt": max_block_index}})
    config.mongo_db.transaction_stats.remove({"block_index": {"$gt": max_block_index}})
    
    #to roll back the state of the tracked asset, dive into the history object for each asset that has
    # been updated on or after the block that we are pruning back to
    assets_to_prune = config.mongo_db.tracked_assets.find({'_at_block': {"$gt": max_block_index}})
    for asset in assets_to_prune:
        logger.info("Pruning asset %s (last modified @ block %i, pruning to state at block %i)" % (
            asset['asset'], asset['_at_block'], max_block_index))
        prev_ver = None
        while len(asset['_history']):
            prev_ver = asset['_history'].pop()
            if prev_ver['_at_block'] <= max_block_index:
                break
        if prev_ver:
            if prev_ver['_at_block'] > max_block_index:
                #even the first history version is newer than max_block_index.
                #in this case, just remove the asset tracking record itself
                config.mongo_db.tracked_assets.remove({'asset': asset['asset']})
            else:
                #if here, we were able to find a previous version that was saved at or before max_block_index
                # (which should be prev_ver ... restore asset's values to its values
                prev_ver['_id'] = asset['_id']
                prev_ver['_history'] = asset['_history']
                config.mongo_db.tracked_assets.save(prev_ver)
    
    #call any rollback processors for any extension modules
    RollbackProcessor.run_active_functions(max_block_index)
    
    config.state['last_message_index'] = -1
    config.state['caught_up'] = False
    cache.blockinfo_cache.clear()
    latest_block = config.mongo_db.processed_blocks.find_one({"block_index": max_block_index}) or config.LATEST_BLOCK_INIT
    return latest_block
