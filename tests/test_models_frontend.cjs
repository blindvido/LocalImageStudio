const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const code = source.slice(source.indexOf('let connectionSequence = 0;'), source.indexOf('function fillConfig()'));
async function main() {
  const nodes = Object.fromEntries(['api-url','api-key','api-model','api-model-select','model-list-state','api-connection-state'].map(id => [id, {value:'',textContent:''}]));
  nodes['api-url'].value='https://example.com/v1'; nodes['api-model'].value='current';
  let resolve, received, choices;
  const context = {$: id => nodes[id], api: (path, method, body) => {received=body; return new Promise(r => resolve=r);}, options: (node, values, selected) => {choices=values;node.value=selected;}};
  vm.createContext(context); vm.runInContext(code,context);
  const pending = context.fetchModels();
  nodes['api-url'].value='https://changed.example/v1'; context.invalidateConnection();
  resolve({models:['old-model']}); await pending;
  assert.equal(choices.length, 1, 'old connection response must not populate new connection');
  const fresh = context.fetchModels(); resolve({models:['one','two']}); await fresh;
  assert.equal(choices.length, 3);
  assert.equal(nodes['api-model'].value, 'current', 'fetch must not silently switch the model');
  assert.equal(received.api_key, undefined, 'blank key uses saved key on the server');
  context.state = {selected:'workflow', config:{api_url:nodes['api-url'].value,model:'current'}};
  context.saveConfig = async () => {};
  context.toast = () => {};
  nodes['template-select'] = {value:'tags'};
  const test = context.testApiConnection(); await new Promise(setImmediate);
  context.invalidateConnection();
  resolve({positive_prompt:'flower',negative_prompt:''}); await test;
  assert.equal(nodes['api-connection-state'].textContent, '连接信息已变化，尚未测试。', 'a result from a cleared saved key must not report success');
  console.log('Model selection and stale connection tests passed');
}
main().catch(e => {console.error(e);process.exitCode=1;});
