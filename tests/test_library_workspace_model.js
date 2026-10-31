"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const player=require("../web/local-player.js");
const workspace=require("../web/library-workspace.js");

test("waveform bins preserve transients across every sample",()=>{
  const samples=new Float32Array([0,.1,-.8,0,.2,0,.9,0]);
  const values=player.peaks(samples,2);
  assert.equal(values.length,2);
  assert.ok(Math.abs(values[0]-.8)<1e-6);
  assert.ok(Math.abs(values[1]-.9)<1e-6);
  assert.deepEqual(player.peaks(new Float32Array(),720),[]);
  assert.equal(player.peaks(samples,100).length,samples.length);
});

test("marker display rejects invalid positions, orders cues and keeps selection labels",()=>{
  const result=player.normaliseMarkers([{time:20,name:"Drop",proposed:true,selected:true,id:"drop"},{start:-1,name:"Bad"},{time:"not a number"},{Start:0,Name:"Intro"},{seconds:Infinity}]);
  assert.deepEqual(result.map(row=>row.start),[0,20]);
  assert.equal(result[0].name,"Intro");
  assert.equal(result[1].proposed,true);
  assert.equal(result[1].selected,true);
  assert.equal(result[1].id,"drop");
  assert.equal(player.time(65.9),"1:05");
  assert.equal(player.time(Infinity),"0:00");
});

test("scanned files with a zero count do not imply absence of Rekordbox cues",()=>{
  const result=workspace.cueSummary({source:"scan",cue_count:0});
  assert.equal(result.known,false);
  assert.equal(result.label,"Cues unknown");
  assert.match(result.detail,/Unknown does not mean no cues/);
});

test("cue evidence distinguishes confirmed zero and an outdated prepared copy",()=>{
  const none=workspace.cueSummary({source:"rekordbox_xml",cue_evidence:{known:true,cue_count:0,loop_count:0,grid_count:1}});
  assert.equal(none.known,true);assert.equal(none.tone,"empty");assert.match(none.label,/0 cues/);
  const prepared=workspace.cueSummary({cue_evidence:{known:true,cue_count:6,loop_count:2,grid_count:3,stale:true}});
  assert.equal(prepared.known,true);assert.equal(prepared.stale,true);assert.equal(prepared.tone,"warning");
  assert.equal(prepared.label,"6 cues · 2 loops");assert.match(prepared.detail,/Snapshot needs refresh/);
  const unknown=workspace.cueSummary({source:"rekordbox_xml",cue_count:0,cue_evidence:{known:false,status:"unknown"}});
  assert.equal(unknown.known,false);
});
