# herdr inside cmux: hide cmux's surface IDs from herdr panes so cmux's Claude hooks
# don't record herdr's sessions against the hosting tab (which makes cmux restore
# `claude --resume` there instead of re-running herdr). Notifications are posted by
# ~/.claude-accounts/harrison/hooks/herdr-cmux-notify.sh against HERDR_HOST_CMUX_SURFACE.
if [[ -n $HERDR_ENV && -n $CMUX_SURFACE_ID ]]; then
  export HERDR_HOST_CMUX_SURFACE=$CMUX_SURFACE_ID
  unset CMUX_SURFACE_ID CMUX_PANEL_ID CMUX_TAB_ID CMUX_WORKSPACE_ID
fi
