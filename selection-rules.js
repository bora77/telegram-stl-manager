/* Shared by the catalog preview and its routing checks. No filesystem writes. */
const SelectionRules = (() => {
  const root = '!! 3D STLs - INCOMING';
  const normalize = value => value.toLowerCase().replace(/[^a-z0-9]/g, '');
  const validFolder = value => typeof value === 'string' && value.trim() === value &&
    value.length > 0 && value !== '.' && value !== '..' &&
    !/[\\/<>:"|?*\x00-\x1f]/.test(value) && !/[. ]$/.test(value) && !value.startsWith('- !');
  function defaultFolder(name) {
    let folder=String(name).replace(/[\\/<>:"|?*\x00-\x1f]/g,'_').trim().replace(/[. ]+$/g,'').trim()||'Artist';
    if(folder.startsWith('- !')||/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(folder))folder='_'+folder;
    return folder;
  }
  function suggest(name, folders) {
    const matches = folders.filter(folder => normalize(folder) === normalize(name));
    return matches.length === 1 ? matches[0] : '';
  }
  function defaultStartMonth(timestamp = new Date()) {
    const date = new Date(timestamp);
    if (!Number.isFinite(date.getTime())) throw Error('Invalid subscription date');
    const parts = new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Berlin',year:'numeric',month:'2-digit'}).formatToParts(date);
    const year = Number(parts.find(p=>p.type==='year').value), month = Number(parts.find(p=>p.type==='month').value);
    return new Date(Date.UTC(year, month-2, 1)).toISOString().slice(0,7);
  }
  function migrate(record, inventory) {
    const scope = !record.download_scope || record.download_scope === 'future'
      ? {download_scope:'from_month',start_month:record.start_month||defaultStartMonth(record.subscribed_at)}
      : {download_scope:record.download_scope};
    if (record.selection_version === 2) return {...record, layout:'monthly', ...scope};
    const old = String(record.relative_directory || '').replaceAll('\\', '/');
    const folder = old.startsWith(root + '/') ? old.slice(root.length + 1) : '';
    return {...record, selection_version: 2,
      creator_folder: validFolder(folder) ? folder : '',
      layout: 'monthly', legacy_directory: old,
      ...scope};
  }
  function validate(record, allowed) {
    if (!allowed.has(record.topic_url)) return 'The creator is outside the approved catalog.';
    if (!validFolder(record.creator_folder)) return 'Choose a creator folder directly inside the incoming folder.';
    if (record.layout !== 'monthly') return 'New downloads must use monthly release folders.';
    if (!['from_month', 'all_and_future'].includes(record.download_scope)) return 'Choose the download scope for this creator.';
    if (record.download_scope==='from_month'&&!/^20\d{2}-(0[1-9]|1[0-2])$/.test(record.start_month||'')) return 'Choose the starting month and year.';
    return '';
  }
  function destination(record, releaseMonth, filename) {
    if (!validFolder(record.creator_folder)) throw Error('Invalid creator folder');
    if (!validFolder(filename)) throw Error('Invalid filename');
    if (record.layout !== 'monthly') throw Error('New downloads must use monthly release folders');
    const parts = [root, record.creator_folder];
    if (record.layout === 'monthly') {
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(releaseMonth || '')) throw Error('Release month needs review');
      parts.push(releaseMonth);
    }
    return parts.concat(filename).join('/');
  }
  function exportRecord(record) {
    return {creator:record.creator, topic_url:record.topic_url,
      creator_folder:record.creator_folder,
      relative_directory:root + '/' + record.creator_folder,
      layout:record.layout, download_scope:record.download_scope,
      ...(record.download_scope==='from_month'?{start_month:record.start_month}:{}),
      ...(record.layout === 'monthly' ? {month_basis:'release', month_format:'YYYY-MM', unknown_month:'needs_review'} : {})};
  }
  return {root, validFolder, defaultFolder, suggest, defaultStartMonth, migrate, validate, destination, exportRecord};
})();
if (typeof module !== 'undefined') module.exports = SelectionRules;
