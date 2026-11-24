// Run the actual UI script with a synthetic API response and no network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function check(staffKind, userView) {
  const ids = ['person-title','person-subtitle','person-profile-summary','person-profile-history'];
  const elements = Object.fromEntries(ids.map(id => [id,{textContent:'',innerHTML:''}]));
  const response = {
    person:{display_name:'Fixture'}, awards:[], difficult_coverage:[],
    bag_memberships:[{staff_kind:staffKind,order_number:1,position_scope:'specialty',
      document_date_iso:'2026-07-01',collective:'PROVISIONALS'}],
    ...(userView ? {user_view:userView} : {}),
  };
  const context={
    window:{},document:{getElementById:id=>elements[id]||null},
    sessionStorage:{getItem:()=>JSON.stringify({normalizedName:'FIXTURE'})},
    localStorage:{getItem:()=>null,removeItem:()=>{}},
    fetch:async()=>({ok:true,json:async()=>response}),URLSearchParams,console,
  };
  vm.runInNewContext(fs.readFileSync('app/web/static/js/person_detail.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return elements;
}

(async()=>{
  const career=await check('career_practice',{display_name:'Fixture',current_result:'career_practice_participant',
    current_result_label:'Participante de carrera/prácticas',participant_order:1,bag_position:null});
  assert.match(career['person-profile-summary'].innerHTML,/Orden en el procedimiento/);
  assert.doesNotMatch(career['person-profile-summary'].innerHTML,/Posición en bolsa/);
  assert.match(career['person-profile-history'].innerHTML,/Carrera\/prácticas/);
  const fallback=await check('unknown',null);
  assert.match(fallback['person-profile-summary'].innerHTML,/Procedimiento sin identificar/);
  assert.doesNotMatch(fallback['person-profile-summary'].innerHTML,/Posición en bolsa/);
  const interino=await check('interino',null);
  assert.match(interino['person-profile-summary'].innerHTML,/Posición en bolsa/);
  console.log('3 UI procedure cases passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
