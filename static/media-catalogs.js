/* 媒体字典页面共用交互；配置错误显示在弹窗内，切换媒体不混用库存。 */
function createMediaCatalogUI(ui) {
  const {api, esc, table, opt, badge, fmt, has, modal, render, feedback} = ui;
  const selections = {
    vehicles: {media: '', q: '', page: 1, size: 20},
    cities: {media: '', q: '', page: 1, size: 20}
  };
  const titles = {vehicles: '车型库', cities: '城市库'};
  const defaults = {
    vehicles: 'http://yiche.asoapple.cn/api/v1/pullCarList',
    cities: 'http://yiche.asoapple.cn/api/v1/pullCityList'
  };
  // 字段顺序对应pullCarList文档；名称、业务编号与接口行id分别展示。
  const vehicleColumns = [
    ['id', '记录ID'], ['carId', '车款编号'], ['carName', '车款名称'],
    ['seriesId', '车系编号'], ['seriesName', '车系名称'],
    ['factoryId', '厂商编号'], ['factoryName', '厂商名称'],
    ['brandId', '品牌编号'], ['brandName', '品牌名称'],
    ['year', '年款'], ['guidePrice', '指导价'], ['displacement', '排量'],
    ['power', '功率'], ['gearbox', '变速箱'], ['saleStatus', '售卖状态（原值）'],
    ['memberBrandName', '会员品牌名称']
  ];
  let activeKind = 'vehicles';

  async function view(kind) {
    activeKind = kind;
    const selection = selections[kind];
    const libraries = await api('media-catalogs/' + kind);
    const selected = libraries.find(row => row.id === selection.media) || libraries[0];
    const intro = `<div class="page-title"><div><div class="eyebrow">MEDIA CATALOG / 媒体字典</div>
      <h1>${titles[kind]}管理</h1><p>按最终媒体独立管理，线索上报自动使用对应媒体编码。</p></div>
      <div class="actions"><button data-nav="partners">管理媒体分类</button><button data-action="refresh">刷新状态</button></div></div>`;
    if (!selected) return intro + '<section class="card"><p class="empty">请先在合作与渠道中添加媒体。</p><button data-nav="partners">合作与渠道 →</button></section>';
    selection.media = selected.id;
    const params = new URLSearchParams({q: selection.q, page: selection.page, page_size: selection.size});
    const result = await api(`media-catalogs/${kind}/${encodeURIComponent(selected.id)}?${params}`);
    const latest = selected.runs[0];
    const status = !selected.configured ? '待配置' :
      ({RUNNING: '同步中', SUCCESS: '同步成功', FAILED: '同步失败'})[latest?.state] || '等待同步';
    const buttons = has('settings') ? `<button data-action="catalog-config">API配置</button>
      <button data-action="catalog-sync" class="primary" ${!selected.configured || latest?.state === 'RUNNING' || selected.legacy ? 'disabled' : ''}>立即同步</button>` : '';
    const headers = kind === 'vehicles' ? vehicleColumns.map(([key, label]) =>
      `${label}<span class="muted tiny catalog-field-key">${key}</span>`) : ['省份', '城市', '城市编号', '省份编号'];
    const rows = result.items.map(row => (kind === 'vehicles' ?
      vehicleColumns.map(([key]) => row[key] ?? '—') :
      [row.parent_name, row.city_name, row.city_code, row.parent_code]).map(esc));
    return intro + `<section class="card"><div class="toolbar">
      <form class="filter-row catalog-filter-form" id="catalog-search-form" data-kind="${kind}">
        <label>媒体分类<select name="media" id="catalog-media">${libraries.map(row => opt(row.id, row.name, selected.id)).join('')}</select></label>
        <label>关键词<input name="q" aria-label="搜索${titles[kind]}" placeholder="${kind === 'cities' ? '省份、城市或编码' : '品牌、厂商、车系或车款'}" value="${esc(selection.q)}"></label>
        <button type="submit">搜索</button></form><div class="actions">${buttons}</div></div>
      <div class="toolbar"><span>${badge(status)} · 共 ${result.total} 条</span>
        <span class="muted">最近成功 ${esc(fmt(selected.last_success))} · ${selected.enabled ? '每日自动同步 ' + String(selected.schedule_hour).padStart(2, '0') + ':00' : '每日自动同步已暂停（仍可手动同步）'}</span></div>
      ${selected.legacy ? '<div class="callout">当前保留旧签名车型库。保存新API配置后将切换同步来源，已有车型保留到新API完整同步成功。</div>' : ''}
      ${latest?.error ? `<div class="callout error" role="alert">${esc(latest.error)}</div>` : ''}
      ${!selected.configured ? '<div class="callout">该媒体尚未配置API，保持空库，不会使用其他媒体编号补齐。</div>' : ''}
      ${kind === 'vehicles' ? '<p class="smalltext muted catalog-field-note">按接口原字段展示，可横向滚动查看全部字段。品牌名称可能是“212”等数字；记录ID、品牌编号、车系编号与车款编号不能互相替代。售卖状态保留接口原值，缺失字段显示“—”。</p>' : ''}
      <div class="${kind === 'vehicles' ? 'catalog-vehicle-table' : ''}">${table(headers, rows)}</div>
      <div class="pagination"><span>共 ${result.total} 条</span><div class="pager"><select id="catalog-size" aria-label="每页条数">${[20, 50, 100].map(size => opt(String(size), size + ' 条', String(selection.size))).join('')}</select>
        <button data-action="catalog-prev" ${selection.page <= 1 ? 'disabled' : ''}>‹</button>
        <span>第 ${selection.page} / ${Math.max(1, Math.ceil(result.total / selection.size))} 页</span>
        <button data-action="catalog-next" ${!result.has_more ? 'disabled' : ''}>›</button></div></div></section>
      <section class="card"><div class="card-head"><h2>最近同步记录</h2><span class="muted">失败保留上次完整字典</span></div>
        ${table(['开始时间', '触发方式', '状态', '页数', '条数', '原因'], selected.runs.map(run => [
          esc(fmt(run.started_at)), run.trigger_kind === 'auto' ? '定时' : '手动',
          badge(({RUNNING: '同步中', SUCCESS: '成功', FAILED: '失败'})[run.state]), run.pages, run.item_count, esc(run.error || '—')
        ]))}</section>`;
  }

  async function action(name) {
    const selection = selections[activeKind];
    if (name === 'catalog-prev' || name === 'catalog-next') {
      selection.page += name === 'catalog-next' ? 1 : -1;
      await render();
    } else if (name === 'catalog-sync') {
      await api(`media-catalogs/${activeKind}/${encodeURIComponent(selection.media)}/sync`, 'POST', {});
      await render();
    } else if (name === 'catalog-config') {
      const library = (await api('media-catalogs/' + activeKind)).find(row => row.id === selection.media);
      if (!library) throw new Error('媒体已变化，请刷新');
      const isYiche = /易车/.test(library.name);
      modal(`${library.name} · ${titles[activeKind]}API配置`, `<form id="catalog-config-form" class="grid-form" data-kind="${activeKind}" data-media="${esc(library.id)}" data-version="${library.version}">
        <div class="callout span2">当前支持GET、errorCode=1、message.data格式。车型分页读取count/total/page，城市一次读取。其他媒体须根据其文档接入，未提供接口时保持空白。</div>
        <label class="span2">字典API地址<input type="url" name="endpoint" value="${esc(library.endpoint || (isYiche ? defaults[activeKind] : ''))}" placeholder="填写该媒体提供的字典API" required></label>
        <label>Bearer Token（无鉴权可留空）<input name="token" type="password" autocomplete="new-password" placeholder="${library.has_token ? '已保存，留空保留' : '未配置'}"></label>
        <label>清除已保存Token<select name="clear_token">${opt('no', '不清除')}${opt('yes', '清除')}</select></label>
        <label>每日自动同步<select name="enabled">${opt('no', '暂停（仍可手动同步）', library.enabled ? 'yes' : 'no')}${opt('yes', '启用', library.enabled ? 'yes' : 'no')}</select></label>
        <label>每天北京时间<select name="schedule_hour">${Array.from({length: 24}, (_, hour) => opt(String(hour), String(hour).padStart(2, '0') + ':00', String(library.schedule_hour))).join('')}</select></label>
        <p class="smalltext muted span2">易车当前两个API无需Token。保存API后即可点击“立即同步”，无需启用每日自动同步。字典读取不会开启线索发送。</p>
        <div class="form-actions span2"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary">保存配置</button></div></form>`);
    }
  }

  async function submit(form, data) {
    if (form.id === 'catalog-search-form') {
      Object.assign(selections[form.dataset.kind], {media: data.media, q: data.q, page: 1});
      await render();
    } else if (form.id === 'catalog-config-form') {
      try {
        await api(`media-catalogs/${form.dataset.kind}/${encodeURIComponent(form.dataset.media)}/config`, 'PATCH', {
          endpoint: data.endpoint, token: data.token, clear_token: data.clear_token === 'yes',
          enabled: data.enabled === 'yes', schedule_hour: Number(data.schedule_hour), version: Number(form.dataset.version)
        });
        document.querySelector('#dialog').close();
        await render();
      } catch (error) {
        feedback(form, error.message, true);
      }
    }
  }

  async function change(target) {
    if (target.id === 'catalog-media') {
      selections[activeKind].media = target.value;
      selections[activeKind].page = 1;
      await render();
    } else if (target.id === 'catalog-size') {
      selections[activeKind].size = Number(target.value);
      selections[activeKind].page = 1;
      await render();
    }
  }

  return {view, action, submit, change};
}
