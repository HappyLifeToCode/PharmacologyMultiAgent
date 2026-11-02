const {test}=require('node:test');
const assert=require('node:assert/strict');
const {format}=require('../server/static/task-clock.js');
test('live elapsed time advances and final elapsed time freezes',()=>{
  const start='2026-11-02T00:00:00Z';
  assert.equal(format(start,null,Date.parse(start)+61000),'1分 1秒');
  assert.equal(format(start,'2026-11-02T00:01:01Z',Date.parse(start)+900000),'1分 1秒');
  assert.equal(format(start,null,Date.parse(start)+3661000),'1小时 1分 1秒');
});
test('missing start is explicit and backward clock changes do not become negative',()=>{
  assert.equal(format(null,null),'尚未开始');
  assert.equal(format('2026-11-02T00:00:00Z',null,0),'0分 0秒');
});
