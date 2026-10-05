"""gunnery —— 攻击模块（主炮对决）：弹道物理 / 武器数据 / 敌舰 AI / 炮战场景。

与雷达控制台仅通过 AttackView 一个入口耦合：
    from gunnery.attack_view import AttackView
    view = AttackView(own_hdg=..., own_spd=...)
    view.battle_closed.connect(...)   # str 战报
"""
