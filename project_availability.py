"""当前需求可见性：主体总开关与主体项目开关同时生效。"""


def demand_project_condition(alias: str = 'd') -> str:
    """返回内部固定别名的可见性条件，旧UNKNOWN需求沿用主体默认项目。

    仅用于当前需求列表/汇总，不用于历史交付和回调读取。
    """
    if alias not in ('d', 'demands'):
        raise ValueError('Unsupported internal demand alias')
    return f'''EXISTS (
        SELECT 1 FROM partners owner JOIN partner_projects project
          ON project.org_id=owner.org_id AND project.partner_id=owner.id
        WHERE owner.org_id={alias}.org_id AND owner.id={alias}.buyer_id
          AND owner.enabled=1 AND project.enabled=1
          AND project.project=CASE WHEN {alias}.sale_project IN ('MEMBER','KA')
            THEN {alias}.sale_project ELSE COALESCE(owner.cooperation_project,'MEMBER') END
    )'''


SOURCE_PROJECT_AVAILABLE_SQL = '''EXISTS (
    SELECT 1 FROM partners owner JOIN partner_projects project
      ON project.org_id=owner.org_id AND project.partner_id=owner.id
    WHERE owner.org_id=s.org_id AND owner.id=s.partner_id
      AND owner.enabled=1 AND project.enabled=1 AND project.project=s.sale_project
)'''
