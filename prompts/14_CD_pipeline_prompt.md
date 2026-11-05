So now that resources are created, resource groups, ACRs and AKS are created right now. I just want to, I just want you to create cd.yml files. So whatever the built image is that we are building in ci.yml, I just want to take that image and push that into ACR. Then I just want to deploy that image. I just want to take the latest image with the latest tag and then put it into the AKS cluster.

If needed, create any Kubernetes, ML files, Helm configurations, or anything like that that is required. If those Helm configurations are required for our project, try to have those configurations. If not, build that CD pipeline which does this: placing the image and then deploying our code into AKS.

Let me know if you have any questions at this point in time. If not, give me the plan. I'll approve the plan such that you can start building the pipelines for us.


1. use your recommend (SHA) no needed of latest everytime
2. you can do that
3. you can go automatically

implment 1,2,3 that you mentioned above.