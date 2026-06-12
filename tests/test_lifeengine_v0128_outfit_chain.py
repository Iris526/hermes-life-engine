import os
import tempfile


def _rt():
    os.environ['HERMES_HOME'] = tempfile.mkdtemp()
    from lifeengine.runtime import LifeEngineRuntime
    return LifeEngineRuntime()


def test_v0128_outfit_resolver_snapshot_purchase_chain():
    from lifeengine.constants import PLUGIN_VERSION
    from lifeengine.db import _SCHEMA_VERSION
    assert PLUGIN_VERSION == '0.13.0'
    assert _SCHEMA_VERSION >= 43
    rt = _rt()
    try:
        assert rt.collection('init')['ok']
        item = rt.collection('add_item', collection_type='wardrobe', name='浅蓝短上衣', description='轻薄棉混纺')
        assert item['ok']
        res = rt.collection('resolve_outfit', query_text='穿浅蓝那套')
        assert res['ok']
        assert res['resolution']['resolved_refs']['wardrobe']['item_id'] == item['item']['id']
        assert res['resolution']['resolved_refs']['sock_drawer']['state'] in {'bare_legs', 'missing'}
        check = rt.collection('asset_check', resolution_id=res['resolution']['id'])
        assert check['check']['status'] == 'needs_generation'
        wear = rt.collection('wear_outfit', resolution_id=res['resolution']['id'])
        assert wear['ok']
        assert rt.collection('current_outfit')['current_outfit']['status'] == 'wearing'
        ret = rt.collection('return_outfit')
        assert ret['ok']
        assert rt.collection('current_outfit')['current_outfit'] is None
    finally:
        rt.close()


def test_v0128_purchase_chain_consumes_defined_resource():
    rt = _rt()
    try:
        rt.collection('init')
        rt.resources('define', key='money.lingzhu', display_name='灵铢', resource_class='fungible', unit='lingzhu', min_value=0, max_value=999999, initial=50)
        out = rt.collection('purchase_chain', collection_type='shoe_cabinet', name='白短靴', price=12, money_key='money.lingzhu')
        assert out['ok']
        assert out['purchase_chain']['collection_item_id']
        resources = rt.resources('list')
        acct = [a for a in resources['resources']['accounts'] if a['resource_key'] == 'money.lingzhu'][0]
        assert float(acct['current_value']) == 38.0
    finally:
        rt.close()
