-- Invoked by test_bridge.lua with its existing assertion helpers.
return function(B, ok, eq)
  local saved={}
  for k,v in pairs(reaper) do saved[k]=v end
  local source={guid="source",name="Reference",items={}}
  local target={guid="target",name="Drums",items={}}
  local master={guid="master"}
  local project={}
  local take={guid="take",rate=1,pitch=0,offset=.8,notes={}}
  local item={guid="item",position=12.5,length=4,take=take}
  source.items={item}
  local state={selected=1,playing=0,stretch=0,takefx=0,parent=nil,project=project,source_length=10,fail_note=false,corrupt=false}
  local function time_ppq(t) return t<=14 and t*1920 or 14*1920+(t-14)*2880 end
  local mocks={
    CountTracks=function() return 2 end,
    GetTrack=function(_,i) return ({source,target})[i+1] end,
    GetMasterTrack=function() return master end,
    GetTrackGUID=function(t) return t.guid end,
    GetTrackName=function(t) return true,t.name end,
    CountTrackMediaItems=function(t) return #t.items end,
    GetTrackMediaItem=function(t,i) return t.items[i+1] end,
    GetActiveTake=function(it) return it.take end,
    CountSelectedMediaItems=function() return state.selected end,
    GetSelectedMediaItem=function() return item end,
    GetMediaItemTrack=function() return source end,
    GetMediaTrackInfo_Value=function() return 1 end,
    GetSetMediaItemInfo_String=function(it,k,v,write)
      if k=="GUID" then return true,it.guid end
      if write then it.job=v end
      return true,it.job or ""
    end,
    GetSetMediaItemTakeInfo_String=function(t,k,v,write)
      if k=="GUID" then return true,t.guid end
      if write then t.name=v end
      return true,t.name or ""
    end,
    TakeIsMIDI=function(t) return t.midi or false end,
    GetMediaItemTake_Source=function() return "src" end,
    GetMediaSourceType=function() return "WAVE" end,
    GetMediaSourceParent=function() return state.parent end,
    GetMediaSourceFileName=function() return "/recording.wav" end,
    GetMediaSourceLength=function() return state.source_length end,
    GetMediaItemTakeInfo_Value=function(t,k) return ({D_PLAYRATE=t.rate,D_PITCH=t.pitch,D_STARTOFFS=t.offset})[k] end,
    GetMediaItemInfo_Value=function(it,k) return ({D_POSITION=it.position,D_LENGTH=it.length})[k] end,
    GetTakeNumStretchMarkers=function() return state.stretch end,
    CountTakeEnvelopes=function() return 0 end,
    TakeFX_GetCount=function() return state.takefx end,
    EnumProjects=function() return state.project,"/project.rpp" end,
    GetPlayState=function() return state.playing end,
    GetTrackMIDINoteNameEx=function() return "Kick" end,
    CreateNewMIDIItemInProj=function(t,a,b)
      local it={guid="new",position=a,length=b-a,take={midi=true,notes={}}}
      t.items[#t.items+1]=it; return it
    end,
    MIDI_GetPPQPosFromProjTime=function(_,t) return time_ppq(t) end,
    MIDI_InsertNote=function(t,sel,mute,a,b,ch,p,v)
      if state.fail_note then return false end
      t.notes[#t.notes+1]={a=a,b=b,ch=ch,p=p,v=state.corrupt and v+1 or v}; return true
    end,
    MIDI_Sort=function(t) table.sort(t.notes,function(a,b) return a.a<b.a end) end,
    MIDI_CountEvts=function(t) return true,#t.notes,0 end,
    MIDI_GetNote=function(t,i)
      local n=t.notes[i+1]; return true,false,false,n.a,n.b,n.ch,n.p,n.v
    end,
    SetMediaItemInfo_Value=function(it,k,v) it[k]=v; return true end,
    DeleteTrackMediaItem=function(t,it)
      for i,v in ipairs(t.items) do if v==it then table.remove(t.items,i); return true end end
      return false
    end,
    UpdateArrange=function() end,
  }
  for k,v in pairs(mocks) do reaper[k]=v end
  local snapshot=B.handlers.get_transcription_source({payload={}})
  eq(snapshot.source_offset,.8,"transcription reads live take trim")
  eq(snapshot.position,12.5,"transcription reads live item position")
  eq(snapshot.item_guid,"item","transcription records stable item identity")
  state.selected=2
  eq(pcall(B.handlers.get_transcription_source,{payload={}}),false,"multiple selected items refused")
  state.selected=1
  for _,field in ipairs({"stretch","takefx"}) do
    state[field]=1
    eq(pcall(B.handlers.get_transcription_source,{payload={}}),false,"processed take refused: "..field)
    state[field]=0
  end
  take.rate=2
  eq(pcall(B.handlers.get_transcription_source,{payload={}}),false,"rate changed source refused")
  take.rate=1; state.parent="reversed"
  eq(pcall(B.handlers.get_transcription_source,{payload={}}),false,"section source refused")
  state.parent=nil; state.source_length=2
  eq(pcall(B.handlers.get_transcription_source,{payload={}}),false,"loop beyond source refused")
  state.source_length=10
  local function payload()
    return {source=snapshot,target_track_guid="target",job_id="drums_test",expected_note_names={},events={
      {type="note",time=.1,duration=.03,pitch=36,velocity=100},
      {type="note",time=2,duration=.03,pitch=36,velocity=104}}}
  end
  local p=payload(); p.dry_run=true
  ok(B.handlers.insert_drum_transcription({payload=p}).validated,"dry run validates source and target")
  eq(#target.items,0,"dry run does not create an item")
  snapshot.length=snapshot.length+1e-12
  ok(B.handlers.insert_drum_transcription({payload=p}).validated,"JSON float rounding does not reject unchanged source")
  snapshot.length=4
  p=payload(); p.expected_note_names={['36']='Snare'}
  eq(pcall(B.handlers.insert_drum_transcription,{payload=p}),false,"changed kit map refused")
  eq(#target.items,0,"changed map causes no mutation")
  item.position=13
  eq(pcall(B.handlers.insert_drum_transcription,{payload=payload()}),false,"moved source refused")
  eq(#target.items,0,"source move causes no mutation")
  item.position=12.5; state.project={}
  eq(pcall(B.handlers.insert_drum_transcription,{payload=payload()}),false,"different project tab refused")
  state.project=project; state.playing=1
  eq(pcall(B.handlers.insert_drum_transcription,{payload=payload()}),false,"active transport refused")
  state.playing=0
  target.items={{guid="existing",position=13,length=1}}
  eq(pcall(B.handlers.insert_drum_transcription,{payload=payload()}),false,"occupied range refused")
  eq(#target.items,1,"existing MIDI is preserved")
  target.items={}; state.fail_note=true
  eq(pcall(B.handlers.insert_drum_transcription,{payload=payload()}),false,"failed note write reports failure")
  eq(#target.items,0,"failed insertion removes only newly created item")
  state.fail_note=false; state.corrupt=true
  eq(pcall(B.handlers.insert_drum_transcription,{payload=payload()}),false,"incorrect note readback fails")
  eq(#target.items,0,"incorrect readback rolls back created item")
  state.corrupt=false
  p=payload(); p.events[2].velocity=100
  eq(pcall(B.handlers.insert_drum_transcription,{payload=p}),false,"velocity repeats fail native verification")
  eq(#target.items,0,"velocity violation removes created item")
  local result=B.handlers.insert_drum_transcription({payload=payload()})
  eq(result.verified,true,"native event readback verified")
  eq(#target.items,1,"one completed transcription item")
  eq(target.items[1].position,12.5,"native insertion uses source timeline position")
  eq(target.items[1].take.notes[2].a,time_ppq(14.5),"tempo changes retain audio seconds")
  eq(target.items[1].B_LOOPSRC,0,"transcription item does not loop")
  eq(pcall(B.handlers.insert_drum_transcription,{payload=payload()}),false,"duplicate job refused by native marker")
  eq(#target.items,1,"duplicate retry adds no notes")
  local markers={}
  reaper.GetMediaItemInfo_Value=function(it,k) return ({D_POSITION=it.position,D_LENGTH=it.length,C_LOCK=state.locked or 0})[k] end
  reaper.GetMediaItemTakeInfo_Value=function(t,k) return ({D_PLAYRATE=t.rate,D_PITCH=t.pitch,D_STARTOFFS=t.offset,B_PPITCH=1})[k] end
  reaper.GetTakeNumStretchMarkers=function() return #markers end
  reaper.GetTakeStretchMarker=function(_,i)
    local m=markers[i+1]; return i,m.position+(state.corrupt_marker and .1 or 0),m.source_position
  end
  reaper.GetTakeStretchMarkerSlope=function() return 0 end
  reaper.GetGlobalAutomationOverride=function() return -1 end
  reaper.UpdateItemInProject=function() end
  reaper.SetTakeStretchMarker=function(_,_,pos,src)
    if state.fail_marker and #markers==1 then return -1 end
    markers[#markers+1]={position=pos,source_position=src}; return #markers-1
  end
  reaper.DeleteTakeStretchMarkers=function() markers={} end
  local timing={edits={{source=snapshot,markers={
    {position=0,source_position=.8},{position=1.01,source_position=1.8},{position=4,source_position=4.8}}}}}
  timing.validate_only=true
  ok(B.handlers.apply_audio_timing({payload=timing}).validated,"timing validates nonzero source offset")
  eq(#markers,0,"timing validation writes nothing")
  timing.validate_only=nil; item.position=13
  eq(pcall(B.handlers.apply_audio_timing,{payload=timing}),false,"timing refuses stale source")
  item.position=12.5; state.locked=1
  eq(pcall(B.handlers.apply_audio_timing,{payload=timing}),false,"timing preserves locked items")
  state.locked=0; state.playing=1
  eq(pcall(B.handlers.apply_audio_timing,{payload=timing}),false,"timing refuses playback")
  state.playing=0; timing.edits[1].markers[2].position=1.1
  eq(pcall(B.handlers.apply_audio_timing,{payload=timing}),false,"timing refuses excessive correction")
  timing.edits[1].markers[2].position=1.01; state.fail_marker=true
  eq(pcall(B.handlers.apply_audio_timing,{payload=timing}),false,"failed timing insertion reports failure")
  eq(#markers,0,"failed timing insertion removes its own markers")
  state.fail_marker=false; state.corrupt_marker=true
  eq(pcall(B.handlers.apply_audio_timing,{payload=timing}),false,"timing verifies native marker values")
  eq(#markers,0,"timing readback failure rolls back")
  state.corrupt_marker=false
  ok(B.handlers.apply_audio_timing({payload=timing}).verified,"timing write verified")
  eq(#markers,3,"timing inserts exact marker count")
  eq(pcall(B.handlers.apply_audio_timing,{payload=timing}),false,"timing refuses retry over existing markers")
  eq(#B.handlers.get_audio_timing({payload={item_guid='item'}}).stretch_markers,3,"timing read returns all markers")
  for k in pairs(reaper) do reaper[k]=nil end
  for k,v in pairs(saved) do reaper[k]=v end
end
