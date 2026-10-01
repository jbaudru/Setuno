# Release Log

## Setuno v1.2.2

- Beat grid locks onto the kicks of the full track: BPM is fine-tuned and bar 1 starts on the downbeat, for every song (including manual-tempo ones). Light theme grid is dark blue; bar labels are smaller themed tags.
- Zooming into the large waveform reveals real detail (down to ~0.25 s across the view) instead of stretching the overview.
- Library loads every song at once (no more "Load more"); mini waveforms are low-resolution cached drawings, keeping large libraries smooth.
- Playlist statistics and the similarity graph use colors from the current theme palette.
- Similarity graph: nodes never overlap, BPM/energy are shown as proper axes with ticks, songs are colored by feature clusters, and titles appear when zoomed in.
- New like button in the player to favorite the current song; it stays in sync with the Library and Playlist Builder.
- Favorites use a modern heart icon (outlined, or filled red when liked) in the player, Library, Playlist Builder and Queue.
- The Library is sorted by title by default.
- The level meter drops to zero as soon as playback stops.

## Setuno v1.2.1

- Waveform previews use 2,400 full-track peak bins, retain end-of-file transients, and refresh older previews in the background.
- Consistent detected beats provide a measured grid phase and precise BPM; unmeasured grids remain hidden. Bar markers are clearer at different zoom levels.
- Startup shows the Setuno logo and a progress indicator until the complete window is ready, without a separate volume-meter flash.