# -*- coding: utf-8 -*-
"""
Layer Tools -- Designer-side helper (Python 2.7, runs inside Designer).

The plugin window calls these through Designer's /api/session/python/execute.
Every public function returns a JSON string.

    info()               version, track, selected layer names
    actions()            the action list shown in the window
    run(action_id)       run one action on every selected layer
    probe()              dump member names of the first selected layer
    find_uid(text)       which layer has this UID: its track and start time
    check_arrows()       read-only: list arrows on the current track that are
                         invalid or point at something that is not a layer of
                         this track

ADDING AN ACTION
    Write a function that takes (layer, track) and returns a dict, and put
    @action(...) on it. It appears in the window the next time it is opened.

        @action('my_thing', 'My thing', 'What it does, one line.')
        def _my_thing(layer, track):
            ...
            return {'note': 'did it'}

    raise Problem('...') to report a failure for that layer (never Exception:
    d3 replaces it, see class Problem).

    For an action that needs every selected layer at once, in selection
    order: @action(..., multi=True, min_layers=2); fn(layers, track) returns
    a list of dicts, each with a 'layer' key.

DESIGN RULES
    * One attempt per edit. If an edit cannot be verified, report it; never
      retry it a different way.
    * Only act on layers that sit directly on the current track.
    * Refuse to create a second blur for a layer that already has one.
    * Problems are reported in 'warnings', not swallowed.
    * Arrows need Modules, which only the playhead hands out. Only the 'Blur'
      button makes arrows: Video -> its own blur layer, transport stopped,
      one attempt, verified.

Documented API used (developer.disguise.one, d3.pyi):
    guisystem.selectedLayers               layers selected in the GUI
    track.addNewLayer(type, t, len, name)  -> new layer
    layer.moduleType / tStart / tLength
    seq.nKeys() / t(i) / key(i).v .r .s    keys, copied with setFloat /
                                           setResource / setStringAtT
    layer.uid                             Resource UID (int)
    layer.fields                           list of FieldSequence
    fieldSequence.setExpression(text)      'self' clears it
    fieldSequence.expression               Expression resource (.error, .isOK)
    Arrow.src / .dest / .srcUid / .destUid / .isValid
    track.arrows, track.makeArrow(module, module) (Blur only),
    guisystem.player.playing/.stopped/.playHead.getModuleForLayer, layer.findSequence,
    notifyEdit, seq.remove / seq.find      (all in the current online d3.pyi;
                                           check against your release)
    d3NetManager.localMachine.hostname / .role
"""
try:
    from d3 import *
except:
    pass


class Problem(ValueError):
    """The plugin's own errors. Never raise Exception here: `from d3 import *`
    replaces it with d3's Exception class, which can't be built from a string
    ("TypeError: Can't construct Exception(str)", 26 Oct)."""

VERSION = '0.9.2'

# How a linked field refers to the same field on another layer. getByUID()
# survives renames; the field is appended with a dot.
LINK_EXPR = u'getByUID(0x%(uid)x).%(field)s'
# Fields whose name starts with a digit ("1st brightness") can't follow
# getByUID(...). ("UnexpectedToken", 27 Oct), but module:<layer>.<field> works,
# so those few are linked by layer name: they break if that layer is renamed.
NAME_LINK_EXPR = u'module:%(layer)s.%(field)s'
# Characters Designer's expression syntax wants as '_' in a field name.
EXPR_UNDERSCORE = u' ()'
LINK_SUFFIX = u' link'
ANCHOR_BOTTOM_EXPR = u'(1 - module:this.size) / 2'
BLUR_CLASS = 'BlurModule'
# Blur only connects these (a standard Video layer; 'VideoModule' in
# Designer is VariableVideoModule in Python, the legacy one is VideoModule).
VIDEO_CLASSES = ('VariableVideoModule', )
BLUR_SUFFIX = u' blur'
BLEND_FIELD = 'blendMode'
BLEND_ALPHA = 1                  # Module.blendMode options: 0 Over, 1 Alpha, 2 Add, 3 Multiply...
OPACITY_FIELD = 'brightness'     # Designer's layer opacity
MAPPING_FIELD = 'mapping'
# Link to parent buttons: (id, button label, tooltip name, fields) per Video
# layer section. Field names as Designer reports them (see the ? probe).
# '*text' = every field whose name contains text (Gain's name isn't known yet).
LINK_GROUPS = (
    ('opacity', 'Opacity', 'opacity', ('brightness',)),
    ('tint', 'Tint', 'Colour X / Colour Y / tint', ('xCol', 'yCol', 'tint.r', 'tint.g', 'tint.b', 'tint.a')),
    ('time', 'Time', 'speed, mode, loop, transition time',
     ('speed', 'mode', 'at end point', 'transition time')),
    ('colour_shift', 'Colour Shift', 'colour shift',
     ('brightness (shift)', 'contrast (scale)', 'saturation scale', 'hue shift', 'RGB controlled',
      'red min', 'red max', 'red gamma', 'green min', 'green max', 'green gamma',
      'blue min', 'blue max', 'blue gamma', '*gain')),
    ('move', 'Move', 'move (size, scale, position, rotation)',
     ('size', 'scale.x', 'scale.y', 'pos.x', 'pos.y', 'rotation')),
    ('crop', 'Crop', 'crop', ('left', 'right', 'top', 'bottom', 'cropSoftness')),
    ('colour', 'Colour', 'colour / exposure',
     ('acesExposure', 'acesGamma', 'cdl', 'ocioExposure', 'ocioContrast', 'ocioGamma')),
)
# Internal names tried for position y, then any field named like pos...y.
POS_Y_FIELDS = ('pos.y', 'position.y', 'offset.y', 'position_y', 'posY', 'pos_y')
CLEAR_EXPR = u'self'
LINK_SKIP = ()
# Only fields whose fs.sequence is one of these get linked. A link on a
# ResourceSequence (video, mapping...) crashes playback with "Bad dynamic_cast"
# in FieldSequence::evalString (seen 2026-10-17), and Designer accepts the
# expression first, so it can't be caught when set.
LINK_SEQ_TYPES = ('FloatSequence', )
# Never call track.duplicateLayer(): it fires TrackWidget.refresh inside the
# plugin's Python ("Access to object of type 'Widget' is not allowed",
# 2026-10-17). Copies are built with addNewLayer instead.
UID_MASK = 0xFFFFFFFFFFFFFFFF

try:
    unicode
except NameError:
    unicode = str
    basestring = str
    long = int


# ---------------------------------------------------------------- JSON

def _esc(s):
    out = []
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif o < 32 or o > 126:
            out.append('\\u%04x' % o if o < 65536 else '?')
        else:
            out.append(ch)
    return ''.join(out)


def _dumps(v):
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, (int, long, float)):
        return repr(v).rstrip('L')
    if isinstance(v, basestring):
        if isinstance(v, str) and not isinstance(v, unicode):
            try:
                v = v.decode('utf-8')
            except:
                v = v.decode('latin-1')
        return '"' + _esc(v) + '"'
    if isinstance(v, dict):
        return '{' + ','.join(_dumps(unicode(k)) + ':' + _dumps(x) for k, x in v.items()) + '}'
    if isinstance(v, (list, tuple)):
        return '[' + ','.join(_dumps(x) for x in v) + ']'
    return _dumps(unicode(repr(v)))


def _ok(**kw):
    kw['ok'] = True
    return _dumps(kw)


def _fail(e, **kw):
    kw['ok'] = False
    kw['error'] = unicode(e)
    return _dumps(kw)


def _errmsg(e):
    try:
        return u'%s: %s' % (type(e).__name__, e)
    except:
        return u'unprintable error'


def _guard(fn, *a):
    """Run fn; whatever is raised, answer with JSON. No sys/traceback imports:
    Designer blocks them (see gus_testpattern v2.6.0)."""
    try:
        return fn(*a)
    except BaseException as e:
        return _dumps({'ok': False, 'error': _errmsg(e)})
    except:
        return _dumps({'ok': False, 'error': u'non-standard error'})


# ---------------------------------------------------------------- lookups

def _gs():
    try:
        return guisystem
    except NameError:
        return None


def _track():
    gs = _gs()
    return getattr(gs, 'track', None) if gs else None


def _layers(track):
    try:
        return list(track.layers)
    except:
        return []


def _lname(layer):
    for n in ('name', 'description'):
        try:
            v = getattr(layer, n)
            if v:
                return unicode(v)
        except:
            pass
    return unicode(repr(layer))


def _selected():
    gs = _gs()
    if gs is None:
        return []
    try:
        sel = gs.selectedLayers
    except:
        return []
    try:
        return [l for l in list(sel) if l is not None]
    except:
        return []


def _uid(res):
    u = getattr(res, 'uid', None)
    if u is None:
        raise Problem('%s has no uid' % _lname(res))
    return long(u) & UID_MASK


def _uid_or_none(res):
    try:
        return _uid(res)
    except:
        return None


def _on_track(track, layer):
    """True when layer sits directly on track (not in a group, not elsewhere)."""
    u = _uid_or_none(layer)
    if u is None:
        return False
    return any(_uid_or_none(l) == u for l in _layers(track))


def _fields(layer):
    try:
        return list(layer.fields)
    except:
        return []


def _fname(fs):
    for n in ('name', 'userName'):
        try:
            v = getattr(fs, n)
            if v:
                return unicode(v)
        except:
            pass
    return u''


def _seq_type(fs):
    try:
        return type(fs.sequence).__name__
    except:
        return None


def _find(track, name):
    for l in _layers(track):
        if _lname(l) == name:
            return l
    return None


def _field_by_name(layer, name):
    try:
        fs = layer.findSequence(name)
        if fs is not None:
            return fs
    except:
        pass
    for fs in _fields(layer):
        if _fname(fs) == name:
            return fs
    return None


# ---------------------------------------------------------------- edits

def _copy_keys(src, dst, warnings):
    """Copy every key of src onto dst (same field on a layer with the same
    start), then drop dst's default keys the original doesn't have."""
    s, d = src.sequence, dst.sequence
    st = type(s).__name__
    times = []
    for i in range(s.nKeys()):
        t, k = s.t(i), s.key(i)
        if st == 'FloatSequence':
            d.setFloat(t, k.v)
        elif st == 'ResourceSequence':
            d.setResource(t, k.r)
        elif st == 'StringSequence':
            d.setStringAtT(t, k.s)
        else:
            raise Problem('no copy for %s' % st)
        times.append(t)
        try:
            d.key(d.find(t)).interpolation = k.interpolation
        except BaseException as e:
            warnings.append(u'%s: interpolation not copied (%s)' % (_fname(src), _errmsg(e)))
    if times:
        for i in reversed(range(d.nKeys())):
            if d.nKeys() > 1 and min(abs(d.t(i) - t) for t in times) > 1e-06:
                d.remove(i, 1)
    try:
        dst.disableSequencing = src.disableSequencing
    except:
        pass
    return len(times)


def _new_layer(track, module_type, layer, name):
    """addNewLayer, then make sure the new layer really is on the track."""
    new = track.addNewLayer(module_type, float(layer.tStart), float(layer.tLength), name)
    if new is None:
        new = _find(track, name)
    if new is None:
        raise Problem('addNewLayer returned nothing and no layer named %s' % name)
    if not _on_track(track, new):
        raise Problem('new layer %s is not on the track; stopping before wiring it up' % name)
    return new


def _unique_name(track, name):
    existing = set(_lname(l) for l in _layers(track))
    base, n = name, 2
    while name in existing:
        name = u'%s %d' % (base, n)
        n += 1
    return name


def _expr_name(name):
    return u''.join(u'_' if ch in EXPR_UNDERSCORE else ch for ch in name)


def _link_expr(src, uid, field):
    """(expression linking to src's field, True if it is by layer name)."""
    name = _expr_name(field)
    if name[:1].isdigit():
        return NAME_LINK_EXPR % {'layer': _expr_name(_lname(src)), 'field': name}, True
    return LINK_EXPR % {'uid': uid, 'field': name}, False


def _name_link_warning(track, src, fields):
    """Log line for fields linked by name; flags a name another layer shares."""
    if not fields:
        return None
    w = u'linked by layer name, break if "%s" is renamed: %s' % (_lname(src), u', '.join(fields))
    if sum(1 for l in _layers(track) if _lname(l) == _lname(src)) > 1:
        w += u' (another layer has the same name, so these may follow the wrong one)'
    return w


def _expr_text(fs):
    """Current expression text on a field, or u'' if none. Reads the
    Expression resource: r32.4 has no FieldSequence.expressionControlled()."""
    ex = getattr(fs, 'expression', None)
    if ex is None:
        return u''
    for a in ('expression', 'expressionString'):
        try:
            v = getattr(ex, a)
            if v:
                return unicode(v).strip()
        except:
            pass
    return u''


def _has_expr(fs):
    return _expr_text(fs) not in (u'', CLEAR_EXPR)


def _notify(layer, fs):
    try:
        fs.notifyEdit()
        return
    except:
        pass
    try:
        layer.notifyEdit(fs)
    except:
        pass


def _set_expr(layer, fs, text):
    """Put an expression on a field. Returns the expression's error text, or
    None when Designer accepted it."""
    fs.setExpression(text)
    _notify(layer, fs)
    ex = getattr(fs, 'expression', None)
    err = None
    try:
        err = ex.error if ex is not None else None
    except:
        pass
    if err:
        return unicode(err)
    try:
        if ex is not None and ex.isOK is False:
            return u'expression not OK'
    except:
        pass
    return None


def _set_const(layer, fs, value):
    """Every key of a number field to value (keeps key times)."""
    seq = fs.sequence
    n = seq.nKeys()
    for i in range(n):
        seq.key(i).v = value
    if not n:
        seq.setFloat(float(layer.tStart), float(value))
    _notify(layer, fs)


def _public(o):
    try:
        return sorted(a for a in dir(o) if not a.startswith('_'))
    except:
        return []


# ---------------------------------------------------------------- arrows

def _arrows(track):
    try:
        return list(track.arrows)
    except:
        return []


def _arrow_valid(a):
    try:
        return a.isValid is not False
    except:
        return True


def _arrows_from(track, src):
    su = _uid_or_none(src)
    out = []
    for a in _arrows(track):
        try:
            if long(a.srcUid) & UID_MASK == su:
                out.append(a)
        except:
            pass
    return out


def _arrow_between(track, src, dst):
    du = _uid_or_none(dst)
    for a in _arrows_from(track, src):
        try:
            if long(a.destUid) & UID_MASK == du:
                return a
        except:
            pass
    return None


# track.makeArrow() only takes Modules ("Cannot convert from 'Layer' to
# 'Module'"), and the only way to get a Module is PlayHead.getModuleForLayer.
# Only the Blur button makes an arrow, with the guards in _blur.

def _arrows_into(track, dst):
    du = _uid_or_none(dst)
    out = []
    for a in _arrows(track):
        try:
            if long(a.destUid) & UID_MASK == du:
                out.append(a)
        except:
            pass
    return out


def _is_blur(layer):
    mt = getattr(layer, 'moduleType', None)
    return mt is not None and BLUR_CLASS in getattr(mt, '__name__', unicode(mt))


def _blurs_for(track, layer):
    """Blur layers on track whose opacity is UID-linked to layer (made by Blur)."""
    tag = u'0x%x' % _uid(layer)
    out = []
    for l in _layers(track):
        if l is layer or not _is_blur(l):
            continue
        op = _field_by_name(l, OPACITY_FIELD)
        if op is not None and tag in _expr_text(op):
            out.append(l)
    return out


def _by_uid_on(track, uid):
    for l in _layers(track):
        if _uid_or_none(l) == uid:
            return l
    return None


def _check_arrows_impl():
    """Read-only. Arrows on the current track that look wrong."""
    track = _track()
    if track is None:
        return _fail('guisystem.track not available')
    uids = set(u for u in (_uid_or_none(l) for l in _layers(track)) if u is not None)
    bad, total, pairs = [], 0, {}
    for a in _arrows(track):
        total += 1
        problems = []
        try:
            su, du = long(a.srcUid) & UID_MASK, long(a.destUid) & UID_MASK
        except BaseException as e:
            bad.append({'arrow': unicode(repr(a)), 'problems': [u'no src/dest uid: ' + _errmsg(e)]})
            continue
        if not _arrow_valid(a):
            problems.append(u'isValid is false')
        if su not in uids:
            problems.append(u'source 0x%x is not a layer on this track' % su)
        if du not in uids:
            problems.append(u'destination 0x%x is not a layer on this track' % du)
        key = (su, du)
        pairs[key] = pairs.get(key, 0) + 1
        if pairs[key] == 2:
            problems.append(u'duplicate arrow between the same two layers')
        if problems:
            try:
                names = u'%s -> %s' % (_lname(a.src), _lname(a.dest))
            except:
                names = u'0x%x -> 0x%x' % (su, du)
            bad.append({'arrow': names, 'problems': problems})
    return _ok(track=_lname(track), arrows=total, suspicious=bad,
               note=u'%d arrows, %d suspicious' % (total, len(bad)))


# ---------------------------------------------------------------- actions

ACTIONS = []


def action(aid, label, desc, multi=False, min_layers=1, group=None):
    """multi=False: fn(layer, track) runs once per selected layer, returns a dict.
    multi=True: fn(layers, track) runs once with every selected layer, in
    selection order, and returns a list of dicts (each with a 'layer' key).
    group: the window shows actions with the same group together under it
    (e.g. 'Link to parent'); None = the main list."""
    def deco(fn):
        ACTIONS.append({'id': aid, 'label': label, 'desc': desc, 'fn': fn,
                        'multi': multi, 'min': min_layers, 'group': group})
        return fn
    return deco


@action('dup_link', 'Duplicate + UID link', 'Duplicate the layer and expression-link every field of the copy to the original by UID, so the copy follows the original.')
def _dup_link(layer, track):
    if not _fields(layer):
        raise Problem('no fields to link (group layer?)')
    uid = _uid(layer)
    name = _unique_name(track, _lname(layer) + LINK_SUFFIX)
    dup = _new_layer(track, layer.moduleType, layer, name)
    linked, copied, failed, warnings, by_name = [], [], [], [], []
    for src in _fields(layer):
        fn = _fname(src)
        fs = _field_by_name(dup, fn) if fn else None
        if fs is None:
            failed.append({'field': fn, 'error': u'not on the new layer'})
            continue
        if fn not in LINK_SKIP and _seq_type(fs) in LINK_SEQ_TYPES:
            expr, named = _link_expr(layer, uid, fn)
            try:
                err = _set_expr(dup, fs, expr)
            except BaseException as e:
                err = _errmsg(e)
            if not err:
                linked.append(fn)
                if named:
                    by_name.append(fn)
                continue
            failed.append({'field': fn, 'error': u'link: ' + err})
            try:
                _set_expr(dup, fs, CLEAR_EXPR)
            except BaseException as e:
                warnings.append(u'%s: could not clear failed link (%s)' % (fn, _errmsg(e)))
        try:
            _copy_keys(src, fs, warnings)
            _notify(dup, fs)
            copied.append(fn)
        except BaseException as e:
            failed.append({'field': fn, 'error': u'copy: ' + _errmsg(e)})
    w = _name_link_warning(track, layer, by_name)
    if w:
        warnings.append(w)
    return {'created': _lname(dup), 'uid': '0x%x' % uid,
            'example': LINK_EXPR % {'uid': uid, 'field': u'<field>'},
            'linked': len(linked), 'copied': copied, 'failed': failed, 'warnings': warnings,
            'note': u'%d linked, %d copied, %d failed' % (len(linked), len(copied), len(failed))}


def _pos_y(layer):
    for n in POS_Y_FIELDS:
        fs = _field_by_name(layer, n)
        if fs is not None:
            return (n, fs)
    for fs in _fields(layer):
        for n in (_fname(fs), unicode(getattr(fs, 'userName', u'') or u'')):
            low = u''.join(ch for ch in n.lower() if ch.isalnum())
            if low.startswith(u'pos') and low.endswith(u'y'):
                return (_fname(fs), fs)
    return (None, None)


@action('anchor_bottom', 'Anchor to bottom', u'Position y = ' + ANCHOR_BOTTOM_EXPR)
def _anchor_bottom(layer, track):
    fn, fs = _pos_y(layer)
    if fs is None:
        raise Problem('no position y field (fields: %s)' % u', '.join(_fname(f) for f in _fields(layer)))
    err = _set_expr(layer, fs, ANCHOR_BOTTOM_EXPR)
    if err:
        raise Problem(u'%s: %s' % (fn, err))
    return {'field': fn, 'note': u'%s = %s' % (fn, ANCHOR_BOTTOM_EXPR)}


def _player():
    gs = _gs()
    return getattr(gs, 'player', None) if gs else None


def _stopped(player):
    """True only when Designer says playback is not running."""
    try:
        if player.playing:
            return False
    except:
        return False
    try:
        return bool(player.stopped or player.holding)
    except:
        return True


def _require_stopped():
    player = _player()
    if player is None:
        raise Problem('guisystem.player not available')
    if not _stopped(player):
        try:
            running = bool(player.playing)
        except:
            running = None
        raise Problem('playback is running: stop the transport, then try again' if running else
                      'could not confirm playback is stopped; not touching the playhead')
    return player


def _blur_pair(layer, track):
    """(source layer, its blur layer or None) from either one selected."""
    if _is_blur(layer):
        srcs = [_by_uid_on(track, long(a.srcUid) & UID_MASK) for a in _arrows_into(track, layer)]
        srcs = [s for s in srcs if s is not None]
        if not srcs:
            srcs = [l for l in _layers(track) if not _is_blur(l) and layer in _blurs_for(track, l)]
        if len(srcs) != 1:
            raise Problem('could not tell which layer this blur belongs to (%d candidates); '
                          'select the layer to blur' % len(srcs))
        return srcs[0], layer
    fed = [_by_uid_on(track, long(a.destUid) & UID_MASK) for a in _arrows_from(track, layer)]
    blurs = [l for l in fed if l is not None and _is_blur(l)]
    for b in _blurs_for(track, layer):
        if all(b is not x for x in blurs):
            blurs.append(b)
    if len(blurs) > 1:
        raise Problem('%d blur layers belong to this layer; select the one to finish' % len(blurs))
    return layer, (blurs[0] if blurs else None)


def _make_blur(layer, track, cls, out):
    """New blur layer: blend mode and mapping copied, opacity UID-linked.
    Returns (blur, problems)."""
    uid = _uid(layer)
    name = _unique_name(track, _lname(layer) + BLUR_SUFFIX)
    blur = _new_layer(track, cls, layer, name)
    out['created'] = _lname(blur)
    problems, warnings = [], out.setdefault('warnings', [])

    src_blend, bb = _field_by_name(layer, BLEND_FIELD), _field_by_name(blur, BLEND_FIELD)
    try:
        _copy_keys(src_blend, bb, warnings)
        _notify(blur, bb)
    except BaseException as e:
        problems.append({'field': u'blend', 'error': _errmsg(e)})

    sm, bm = _field_by_name(layer, MAPPING_FIELD), _field_by_name(blur, MAPPING_FIELD)
    if sm is None or bm is None:
        problems.append({'field': u'mapping', 'error': u'no %s field on the %s layer'
                         % (MAPPING_FIELD, 'selected' if sm is None else 'blur')})
    else:
        try:
            _copy_keys(sm, bm, warnings)
            _notify(blur, bm)
        except BaseException as e:
            problems.append({'field': u'mapping', 'error': _errmsg(e)})

    op = _field_by_name(blur, OPACITY_FIELD)
    if op is None:
        problems.append({'field': u'opacity', 'error': u'blur layer has no %s field' % OPACITY_FIELD})
    else:
        err = _set_expr(blur, op, LINK_EXPR % {'uid': uid, 'field': _expr_name(OPACITY_FIELD)})
        if err:
            problems.append({'field': u'opacity', 'error': err})
    return blur, problems


@action('blur', 'Blur',
        'Video layer -> blur layer: makes a Blur layer over the same time with this layer\'s blend mode '
        'and mapping, opacity UID-linked, pipes this layer into it, then sets this layer to Alpha. '
        'Playback must be stopped. Click again to finish if it stops part way. Select either layer.')
def _blur(layer, track):
    # The one place the plugin uses the playhead (getModuleForLayer, needed
    # for makeArrow). Guards: everything is checked before anything is made;
    # transport stopped; Video -> its own blur only; one attempt per step,
    # verified, never retried another way.
    src, blur = _blur_pair(layer, track)
    mt = getattr(getattr(src, 'moduleType', None), '__name__', u'?')
    if mt not in VIDEO_CLASSES:
        raise Problem('%s is a %s, not a Video layer (%s)' % (_lname(src), mt, u', '.join(VIDEO_CLASSES)))
    for l in (src, blur):
        if l is not None and not _on_track(track, l):
            raise Problem('%s is not directly on the current track' % _lname(l))
    src_blend = _field_by_name(src, BLEND_FIELD)
    if src_blend is None:
        raise Problem('%s has no %s field' % (_lname(src), BLEND_FIELD))
    cls = _cls(BLUR_CLASS)
    if blur is None and cls is None:
        raise Problem('%s class not available' % BLUR_CLASS)

    arrow = _arrow_between(track, src, blur) if blur is not None else None
    if arrow is not None and not _arrow_valid(arrow):
        raise Problem('Designer marks the arrow %s -> %s invalid; delete it by hand' % (_lname(src), _lname(blur)))
    if arrow is None:
        if _arrows_from(track, src):
            raise Problem('%s already feeds another arrow; not adding one' % _lname(src))
        player = _require_stopped()

    out, steps = {}, []
    if blur is None:
        blur, problems = _make_blur(src, track, cls, out)
        if problems:
            out.update(failed=problems, note=u'blur layer made, setup incomplete',
                       next=u'Fix the problems above, or delete "%s"' % _lname(blur))
            return out
        steps.append(u'made "%s"' % _lname(blur))

    if arrow is None:
        ph = player.playHead
        ms, md = ph.getModuleForLayer(src), ph.getModuleForLayer(blur)
        if ms is None or md is None:
            out.update(failed=[{'field': u'arrow', 'error': u'Designer has no module for %s yet'
                                % (_lname(src) if ms is None else _lname(blur))}],
                       note=u', '.join(steps + [u'arrow not made yet']),
                       next=u'Park the playhead (stopped) inside both layers and click Blur again')
            return out
        track.makeArrow(ms, md)
        out['edited'] = True
        arrow = _arrow_between(track, src, blur)
        if arrow is None or not _arrow_valid(arrow):
            out.update(failed=[{'field': u'arrow', 'error': u'makeArrow ran but no arrow appeared'
                                if arrow is None else u'arrow made but Designer marks it invalid; delete it by hand'}],
                       note=u', '.join(steps + [u'arrow FAILED']),
                       next=u'Check the track by hand; %s was not changed' % _lname(src))
            return out
        steps.append(u'arrow made')

    if _key0(src_blend) == BLEND_ALPHA and src_blend.sequence.nKeys() <= 1:
        steps.append(u'%s already Alpha' % _lname(src))
    else:
        _set_const(src, src_blend, BLEND_ALPHA)
        steps.append(u'%s -> Alpha' % _lname(src))
    out['note'] = u', '.join(steps)
    return out

def _key0(fs):
    try:
        v = fs.sequence.key(0).v
        if isinstance(v, float) and v == int(v):
            return int(v)
        return v
    except:
        return None


def _expand_fields(layer, fields):
    """LINK_GROUPS entries starting with '*' match every field on layer whose
    name contains the rest (case-insensitive), for names that vary by build.
    Returns (field names, patterns that matched nothing)."""
    out, unmatched = [], []
    names = [_fname(f) for f in _fields(layer)]
    for f in fields:
        if f.startswith('*'):
            want = f[1:].lower()
            hits = [n for n in names if want in n.lower()]
            if not hits:
                unmatched.append(f[1:])
            for n in hits:
                if n not in out and n not in fields:
                    out.append(n)
        elif f not in out:
            out.append(f)
    return out, unmatched


def _link_fields(layers, track, fields, what):
    """First layer is the parent; every other layer gets each of fields set to
    the parent's same field by UID. Only number fields (LINK_SEQ_TYPES): an
    expression on a resource field crashes playback. A field neither layer
    has is skipped quietly; one only the parent has is a failure."""
    parent, children = layers[0], layers[1:]
    uid = _uid(parent)
    fields, unmatched = _expand_fields(parent, fields)
    results = [{'layer': _lname(parent), 'ok': True, 'note': u'parent 0x%x' % uid}]
    for child in children:
        linked, failed, warnings, by_name = [], [], [], []
        skipped = [u'%s (no field name contains "%s")' % (p, p) for p in unmatched]
        for fn in fields:
            pfs, fs = _field_by_name(parent, fn), _field_by_name(child, fn)
            if pfs is None and fs is None:
                skipped.append(u'%s (not on these layers)' % fn)
                continue
            if pfs is None:
                failed.append({'field': fn, 'error': u'parent has no such field'})
                continue
            if fs is None:
                failed.append({'field': fn, 'error': u'not on this layer'})
                continue
            st = _seq_type(fs)
            if st not in LINK_SEQ_TYPES:
                skipped.append(u'%s (%s, not a number field)' % (fn, st))
                continue
            expr, named = _link_expr(parent, uid, fn)
            try:
                err = _set_expr(child, fs, expr)
            except BaseException as e:
                err = _errmsg(e)
            if err:
                failed.append({'field': fn, 'error': err})
                try:
                    _set_expr(child, fs, CLEAR_EXPR)
                except BaseException as e:
                    warnings.append(u'%s: could not clear failed link (%s)' % (fn, _errmsg(e)))
            else:
                linked.append(fn)
                if named:
                    by_name.append(fn)
        w = _name_link_warning(track, parent, by_name)
        if w:
            warnings.append(w)
        note = u'%d %s fields linked to %s' % (len(linked), what, _lname(parent))
        if skipped:
            note += u'; skipped ' + u', '.join(skipped)
        results.append({'layer': _lname(child), 'ok': not failed, 'linked': linked,
                        'failed': failed, 'warnings': warnings, 'note': note})
    return results


def _link_action(fields, what):
    return lambda layers, track: _link_fields(layers, track, fields, what)


# Loop names are prefixed _lg_ and deleted after: a plain `_fields` here once
# replaced the _fields() helper and broke every action that lists fields.
for _lg_id, _lg_label, _lg_what, _lg_fields in LINK_GROUPS:
    action('link_' + _lg_id, _lg_label,
           u'First selected layer (marked *) is the parent. Every other selected layer gets its %s '
           u'fields linked to the parent by UID: %s.' % (_lg_what, u', '.join(_lg_fields)),
           multi=True, min_layers=2, group=u'Link to parent')(_link_action(_lg_fields, _lg_id.replace('_', ' ')))
del _lg_id, _lg_label, _lg_what, _lg_fields


@action('clear_expr', 'Clear expressions', 'Set every field on the layer back to "self" (removes all expressions, keeps sequenced values).')
def _clear_expr(layer, track):
    n, failed = 0, []
    for fs in _fields(layer):
        try:
            if _has_expr(fs):
                _set_expr(layer, fs, CLEAR_EXPR)
                n += 1
        except BaseException as e:
            failed.append({'field': _fname(fs), 'error': _errmsg(e)})
    return {'cleared': n, 'failed': failed, 'note': u'%d cleared' % n}


@action('copy_uid', 'Show UID', 'Report the layer UID in hex, as used by getByUID().')
def _copy_uid(layer, track):
    return {'uid': '0x%x' % _uid(layer), 'note': u'getByUID(0x%x)' % _uid(layer)}


# Actions that only read, or only set expressions on the selected layer, may
# run on any selected layer. The rest add layers/arrows to the current track.
TRACK_EDITING = ('dup_link', 'blur')


def _cls(name):
    """Class from the d3 names this module received (never imports)."""
    c = globals().get(name)
    if c is None:
        try:
            c = __builtins__.get(name) if isinstance(__builtins__, dict) else getattr(__builtins__, name, None)
        except:
            c = None
    return c


# ---------------------------------------------------------------- find_uid

def _parse_uid(text):
    """UID candidates from '0x1a2b', '1a2b', '12345' or 'getByUID(0x1a2b).x'."""
    t = unicode(text).strip().lower()
    i = t.find(u'0x')
    if i >= 0:
        h = u''
        for ch in t[i + 2:]:
            if ch not in u'0123456789abcdef':
                break
            h += ch
        return [long(h, 16)] if h else []
    t = u''.join(ch for ch in t if ch.isalnum())
    out = []
    if t.isdigit():
        out.append(long(t))
    if t and all(ch in u'0123456789abcdef' for ch in t):
        out.append(long(t, 16))
    return out


def _all_tracks():
    out = []
    t = _track()
    if t is not None:
        out.append(t)
    rm = globals().get('resourceManager')
    cls = _cls('Track')
    if rm is not None and cls is not None:
        try:
            for x in rm.allResources(cls):
                if all(x is not y for y in out):
                    out.append(x)
        except:
            pass
    return out


def _all_layers(track):
    """Layers on a track, including those inside groups."""
    out, todo = [], _layers(track)
    while todo:
        l = todo.pop(0)
        out.append(l)
        try:
            todo.extend(l.layers)
        except:
            pass
    return out


def _by_uid(uid):
    """(resource, track it was found on or None)."""
    for track in _all_tracks():
        for l in _all_layers(track):
            if _uid_or_none(l) == uid:
                return (l, track)
    return (None, None)


def _find_uid_impl(text):
    cands = _parse_uid(text)
    if not cands:
        return _fail(u'not a UID: %s' % text)
    for uid in cands:
        r, track = _by_uid(uid)
        if r is None:
            continue
        out = {'uid': '0x%x' % uid, 'name': _lname(r), 'type': type(r).__name__}
        beat = float(r.tStart)
        try:
            sec = float(track.beatToTime(beat))
        except:
            sec = beat
        cur = _track()
        return _ok(isLayer=True, track=_lname(track), onCurrentTrack=(cur is not None and track is cur), time=sec, **out)
    return _fail(u'no layer with UID %s' % u' / '.join('0x%x' % c for c in cands))


# ---------------------------------------------------------------- entry points

def _machine():
    """(hostname, role) of the Designer running this code."""
    try:
        m = d3NetManager.localMachine
    except:
        return (None, None)
    host, role = None, None
    try:
        host = unicode(m.hostname)
    except:
        pass
    try:
        r = m.role
        role = unicode(r.toString() if hasattr(r, 'toString') else r)
    except:
        pass
    return (host, role)


def _info_impl():
    t = _track()
    host, role = _machine()
    return _ok(version=VERSION, track=_lname(t) if t is not None else None,
               selected=[_lname(l) for l in _selected()], machine=host, role=role)


def _actions_impl():
    return _ok(actions=[{'id': a['id'], 'label': a['label'], 'desc': a['desc'],
                         'multi': a['multi'], 'min': a['min'], 'group': a['group']} for a in ACTIONS])


def _run_impl(aid):
    act = None
    for a in ACTIONS:
        if a['id'] == aid:
            act = a
    if act is None:
        return _fail('no action %s' % aid)
    track = _track()
    if track is None:
        return _fail('guisystem.track not available')
    sel = _selected()
    if len(sel) < act['min']:
        return _fail('select at least %d layer(s)' % act['min'])
    if act['multi']:
        try:
            results = list(act['fn'](sel, track) or [])
        except BaseException as e:
            results = [{'layer': u', '.join(_lname(l) for l in sel), 'ok': False, 'error': _errmsg(e)}]
        return _ok(action=aid, results=results)
    results = []
    for layer in sel:
        r = {'layer': _lname(layer)}
        try:
            if aid in TRACK_EDITING and not _on_track(track, layer):
                raise Problem('layer is not directly on the current track (inside a group, or on another track); skipped')
            r.update(act['fn'](layer, track) or {})
            r['ok'] = not r.get('failed')
        except BaseException as e:
            r.update(ok=False, error=_errmsg(e))
        results.append(r)
    return _ok(action=aid, results=results)


def _probe_impl():
    sel = _selected()
    out = {'version': VERSION, 'guisystem': [a for a in _public(_gs()) if 'select' in a.lower()]}
    if sel:
        l = sel[0]
        fs = _fields(l)
        out.update(layer=_lname(l), layerType=type(l).__name__, uid=getattr(l, 'uid', None),
                   fields=[{'name': _fname(f), 'type': getattr(f, 'typeName', None), 'seq': _seq_type(f), 'expr': _expr_text(f)} for f in fs],
                   fieldSequence=_public(fs[0]) if fs else [])
    else:
        out['layer'] = 'nothing selected'
    return _dumps(out)


def info():
    return _guard(_info_impl)


def actions():
    return _guard(_actions_impl)


def run(action_id):
    return _guard(_run_impl, action_id)


def find_uid(text):
    return _guard(_find_uid_impl, text)


def probe():
    return _guard(_probe_impl)


def check_arrows():
    return _guard(_check_arrows_impl)
