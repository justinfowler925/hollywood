"""Shared, versioned scene contract for the producer, UI and rendering worker."""
VERSION=2
PRESETS={
 'ocean':{'label':'Open ocean','family':'nature','renderers':['godot','blender']},
 'forest':{'label':'Forest clearing','family':'nature','renderers':['godot']},
 'rain_window':{'label':'Rainy city window','family':'places','renderers':['godot']},
 'abstract':{'label':'Flowing geometry','family':'abstract','renderers':['godot']},
 'space':{'label':'Planet and stars','family':'space','renderers':['godot']},
 'music_visualizer':{'label':'Music-reactive geometry','family':'music','renderers':['godot']},
 'studio':{'label':'Product stage','family':'studio','renderers':['godot']},
}
PALETTES={'aqua':[[.02,.7,.75],[.35,.15,.8]],'amber':[[1,.5,.12],[.55,.1,.2]],'violet':[[.55,.18,.95],[.05,.65,.9]]}
CAMERAS=['fixed','drift','orbit']
