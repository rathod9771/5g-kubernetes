"""Pure, standard-library clock-consistency calculation; not a clock-control seam."""
def clock_uncertainty(samples,anchors):
    # Empirical same-kernel consistency envelope, NOT absolute NTP accuracy.
    if len(anchors)<2:return None
    offsets=[]
    for a in anchors:
        if not a['wall_before_ns']<=a['wall_after_ns']:return None
        offsets.extend([a['wall_before_ns']-a['monotonic_ns'],a['wall_after_ns']-a['monotonic_ns']])
    for sample in samples:
        for prefix in ('producer','observer'):
            if sample[prefix+'_wall_ns']>0 and sample[prefix+'_monotonic_ns']>0:offsets.append(sample[prefix+'_wall_ns']-sample[prefix+'_monotonic_ns'])
    bound=max(offsets)-min(offsets)+1000 # libpcap microsecond quantization allowance.
    return bound if 0<=bound<=100_000_000 else None

