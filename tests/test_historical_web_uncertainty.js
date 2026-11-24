// Run real frontend and translation scripts against synthetic API data, offline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function render(lang) {
  const ids=['person-title','person-subtitle','person-profile-summary','person-profile-history'];
  const elements=Object.fromEntries(ids.map(id=>[id,{textContent:'',innerHTML:''}]));
  const response={person:{display_name:'Fixture'},bag_memberships:[],difficult_coverage:[],
    awards:[{id:1,status:'Adjudicat',document_date_iso:'2026-11-03',validation_status:'needs_validation',
      historical_status:'unknown',publication_date_quality:'unknown',extraction_completeness:'partial',assignments:[]}]};
  const document={documentElement:{getAttribute:()=>lang},getElementById:id=>elements[id]||null,
    readyState:'loading',addEventListener:()=>{}};
  const context={window:{},document,MutationObserver:class{observe(){}},
    sessionStorage:{getItem:()=>JSON.stringify({normalizedName:'FIXTURE'})},
    localStorage:{getItem:()=>null,removeItem:()=>{}},fetch:async()=>({ok:true,json:async()=>response}),
    URLSearchParams,console};
  vm.runInNewContext(fs.readFileSync('app/web/static/js/i18n.js','utf8'),context);
  vm.runInNewContext(fs.readFileSync('app/web/static/js/person_detail.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));return elements;
}
(async()=>{
  const es=await render('es');assert.match(es['person-profile-history'].innerHTML,/Validación documental pendiente/);
  assert.match(es['person-profile-history'].innerHTML,/Vigencia y fecha de publicación sin acreditar/);
  assert.match(es['person-profile-history'].innerHTML,/Fecha del documento/);
  assert.match(es['person-profile-history'].innerHTML,/Extracción parcial: quedan registros por revisar/);
  const va=await render('va');assert.match(va['person-profile-history'].innerHTML,/Validació documental pendent/);
  assert.match(va['person-profile-history'].innerHTML,/Vigència i data de publicació sense acreditar/);
  assert.match(va['person-profile-history'].innerHTML,/Data del document/);
  assert.match(va['person-profile-history'].innerHTML,/Extracció parcial: queden registres per revisar/);
  console.log('Frontend uncertainty: Spanish and Valencian passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
