// Remove only the completed handoff from this browser history entry. Reloading
// then restores the working draft, and a later gallery click can import again.
export function consumeHandoffSource(source) {
  const url = new URL(location.href);
  if (url.searchParams.get('from') !== source) return;
  url.searchParams.delete('from');
  history.replaceState(history.state, '', url);
}
