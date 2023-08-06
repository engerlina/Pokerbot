from typing import Optional
import logging


logger = logging.getLogger(__name__)


class MixpanelWrapper:
    def __init__(self, token: Optional[str] = None):
        if token is not None:
            import mixpanel
            from mixpanel_async import AsyncBufferedConsumer
            self.mxp = mixpanel.Mixpanel(token, consumer=AsyncBufferedConsumer())
        else:
            self.mxp = None

    def track(self, distinct_id, event_name, properties):
        if self.mxp is not None:
            try:
                self.mxp.track(distinct_id, event_name, properties)
            except:
                logger.error("Mixpanel error")
        else:
            pass

    def people_set(self, distinct_id, properties):
        if self.mxp is not None:
            try:
                self.mxp.people_set(distinct_id, properties)
            except:
                logger.error("Mixpanel error")
        else:
            pass
