const test = require('node:test');
const assert = require('node:assert/strict');
const rules = require('./selection-rules.js');
const url = 'https://t.me/c/123456789/200';
const record = {creator:'Atlan Forge',topic_url:url,creator_folder:'- Atlan Forge',layout:'monthly',download_scope:'from_month',start_month:'2026-08'};

test('Atlan and Wicked both route by release month under INCOMING', () => {
  assert.equal(rules.destination(record,'2026-08','release.7z'), '!! 3D STLs - INCOMING/- Atlan Forge/2026-08/release.7z');
  assert.equal(rules.destination({...record,creator_folder:'- Wicked'},'2026-02','release.zip'), '!! 3D STLs - INCOMING/- Wicked/2026-02/release.zip');
});
test('missing or invalid release month never falls back to the current month', () => {
  for (const month of [undefined,'','2026-13','2026-00','2026-8','../outside']) {
    assert.throws(() => rules.destination(record,month,'release.zip'), /Release month needs review/);
  }
});
test('paths cannot escape the incoming creator directory', () => {
  for(const name of ['../elsewhere','..','/tmp','C:\\temp','- A/2026-01','- A\\2026-01']) {
    assert.throws(() => rules.destination({...record,creator_folder:name},'2026-08','release.zip'));
  }
  assert.throws(() => rules.destination(record,'2026-08','../other.zip'));
});
test('old incoming paths migrate without losing the chosen scope', () => {
  const migrated=rules.migrate({relative_directory:'!! 3D STLs - INCOMING\\- Wicked',download_scope:'all_and_future'},{});
  assert.equal(migrated.creator_folder,'- Wicked');
  assert.equal(migrated.layout,'monthly');
  assert.equal(migrated.download_scope,'all_and_future');
  assert.equal(rules.migrate({relative_directory:'Other share folder'},{}).creator_folder,'');
});
test('previous flat drafts become monthly; export includes the incoming root', () => {
  const migrated=rules.migrate({...record,selection_version:2,layout:'flat'},{});
  assert.equal(migrated.layout,'monthly');
  assert.deepEqual(rules.exportRecord(migrated),{
    creator:'Atlan Forge',topic_url:url,creator_folder:'- Atlan Forge',relative_directory:'!! 3D STLs - INCOMING/- Atlan Forge',
    layout:'monthly',download_scope:'from_month',start_month:'2026-08',month_basis:'release',month_format:'YYYY-MM',unknown_month:'needs_review'
  });
});
test('scope remains per creator and flat routing is rejected', () => {
  assert.equal(rules.validate(record,new Set([url])), '');
  assert.match(rules.validate({...record,download_scope:''},new Set([url])),/scope/);
  assert.match(rules.validate({...record,layout:'flat'},new Set([url])),/monthly/);
  assert.match(rules.validate(record,new Set()),/approved catalog/);
});
test('folder suggestions preserve existing spelling and avoid ambiguous matches', () => {
  assert.equal(rules.suggest('Atlan Forge',['- Atlan Forge','- Wicked']),'- Atlan Forge');
  assert.equal(rules.suggest('Stainless Minis',['- Stainless Minis','- StainlessMinis']),'');
});
test('custom release month is validated and included in saved settings',()=>{
  const custom={...record,download_scope:'from_month',start_month:'2025-10'};
  assert.equal(rules.validate(custom,new Set([url])),'');
  assert.equal(rules.exportRecord(custom).start_month,'2025-10');
  for(const start_month of ['',null,'2025-13','2025-1'])assert.match(rules.validate({...custom,start_month},new Set([url])),/month/);
});
test('new month defaults handle January and legacy Future retains its saved baseline',()=>{
  assert.equal(rules.defaultStartMonth('2026-01-31T12:00:00Z'),'2025-12');
  assert.equal(rules.defaultStartMonth('2026-09-13T12:00:00Z'),'2026-08');
  assert.equal(rules.defaultStartMonth('2026-03-31T22:30:00Z'),'2026-03');
  const legacy={...record,selection_version:2,download_scope:'future',start_month:'2025-02'};
  const migrated=rules.migrate(legacy,{});
  assert.equal(migrated.download_scope,'from_month');assert.equal(migrated.start_month,'2025-02');
  assert.deepEqual(rules.migrate(migrated,{}),migrated);
  assert.equal(rules.migrate({...legacy,start_month:undefined,subscribed_at:'2026-01-31T12:00:00Z'},{}).start_month,'2025-12');
});
